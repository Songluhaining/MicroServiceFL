"""Build the grey-box endpoint index from Spring Boot jars.

Grey-box means: at diagnosis time the agent has *no source*, only the deployed
artifacts. This step runs **once, offline**, over the ``*-server`` jars and
produces ``endpoint_index.json`` — a map from HTTP endpoint (as it appears in a
trace Entry span) to the controller class + method + owning jar:

    {"DELETE:/admin-api/system/mail-account/delete-list": {
        "class": "cn.iocoder.yudao.module.system.controller.admin.mail.MailAccountController",
        "method": "deleteMailAccountList",
        "jar": "yudao-module-system-server"}, ...}

The heavy lifting is a dependency-free Java reflection scanner
(``EndpointScanner.java``) compiled on the fly with the JDK that built the jars.
Only the endpoint->controller hop is precomputed here; controller->service->impl
is left to on-demand decompilation + the LLM.

Usage::

    python -m microservice_fl.greybox.build_index \
        --jars E:/.../yudao-cloud-mini \
        --out  E:/.../dataset/endpoint_index.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCANNER = _HERE / "EndpointScanner.java"


def _tool(name: str) -> str:
    """Resolve a JDK tool (javac/java), honouring JAVA_HOME then PATH."""
    jhome = os.environ.get("JAVA_HOME")
    if jhome:
        cand = Path(jhome) / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} not found (set JAVA_HOME or add the JDK to PATH)")


def _compile_scanner(workdir: Path) -> Path:
    """Compile EndpointScanner.java into workdir; return the compiled dir."""
    out = workdir / "scanner"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([_tool("javac"), "-encoding", "UTF-8", "-d", str(out), str(_SCANNER)],
                   check=True, capture_output=True, text=True)
    return out


def _find_server_jars(jars_root: Path) -> list[Path]:
    """Locate built ``*-server.jar`` artifacts (skip sources/original)."""
    result: list[Path] = []
    for p in jars_root.rglob("*-server.jar"):
        n = p.name
        if n.endswith((".original",)) or "sources" in n or "javadoc" in n:
            continue
        # prefer target/ artifacts
        if p.parent.name == "target":
            result.append(p)
    return sorted(set(result))


def _artifact_name(jar: Path) -> str:
    """yudao-module-system-server.jar -> yudao-module-system-server."""
    return jar.stem


def _scan_jar(jar: Path, scanner_dir: Path, workdir: Path) -> list[dict]:
    """Extract BOOT-INF, run the scanner, parse its JSONL output."""
    ex = workdir / jar.stem
    ex.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar) as zf:
        members = [m for m in zf.namelist()
                   if m.startswith("BOOT-INF/classes/") or m.startswith("BOOT-INF/lib/")]
        zf.extractall(ex, members)
    classes = ex / "BOOT-INF" / "classes"
    lib = ex / "BOOT-INF" / "lib"
    if not classes.is_dir():
        # not a repackaged boot jar; fall back to jar root
        classes = ex
    cmd = [_tool("java"), "-cp", str(scanner_dir), "EndpointScanner",
           str(classes), _artifact_name(jar)]
    if lib.is_dir():
        cmd.append(str(lib))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if proc.returncode != 0 and not rows:
        sys.stderr.write(proc.stderr[-800:])
    return rows


def build(jars_root: Path, out_path: Path) -> dict:
    index: dict[str, dict] = {}
    collisions = 0
    with tempfile.TemporaryDirectory(prefix="fl_greybox_") as td:
        work = Path(td)
        scanner = _compile_scanner(work)
        jars = _find_server_jars(jars_root)
        if not jars:
            raise SystemExit(f"no *-server.jar under {jars_root} (build them first)")
        for jar in jars:
            rows = _scan_jar(jar, scanner, work)
            print(f"  {jar.name}: {len(rows)} endpoints", file=sys.stderr)
            for r in rows:
                http = r.get("http", "ANY")
                path = r.get("path", "")
                if not path:
                    continue
                key = f"{http}:{path}"
                entry = {"class": r["class"], "method": r["method"], "jar": r["jar"]}
                if key in index and index[key] != entry:
                    collisions += 1
                index[key] = entry
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}  ({len(index)} endpoints, {collisions} collisions)",
          file=sys.stderr)
    return index


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build grey-box endpoint index from jars.")
    ap.add_argument("--jars", type=Path, required=True,
                    help="root dir containing built *-server.jar artifacts")
    ap.add_argument("--out", type=Path, required=True, help="endpoint_index.json path")
    args = ap.parse_args(argv)
    build(args.jars, args.out)


if __name__ == "__main__":
    main()
