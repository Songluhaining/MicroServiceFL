"""On-demand class decompilation from the deployed jars (grey-box, source-free).

The endpoint index gets the agent to a controller class + method; to reason about
the *root cause* and a *fix* it needs the method body. In a grey-box setting
there is no source, only the deployed jar — so we decompile the single suspect
class with CFR. This is the runtime replacement for reading the source tree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from microservice_fl import config


def _java() -> str:
    jhome = os.environ.get("JAVA_HOME")
    if jhome:
        cand = Path(jhome) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if cand.exists():
            return str(cand)
    found = shutil.which("java")
    if found:
        return found
    raise FileNotFoundError("java not found (set JAVA_HOME or add the JDK/JRE to PATH)")


def find_jar_for_class(class_fqn: str, jars_dir: Path | None = None) -> Path | None:
    """Locate the built ``*-server.jar`` that owns a business class."""
    module = config.class_fqn_to_module(class_fqn)
    if not module:
        return None
    jar_name = config.module_to_jar(module) + ".jar"
    root = jars_dir or config.JARS_DIR
    for p in root.rglob(jar_name):
        if p.parent.name == "target":
            return p
    # fall back to any match (e.g. a flat jars dir with no target/)
    for p in root.rglob(jar_name):
        return p
    return None


def decompile_class(
    class_fqn: str,
    *,
    jars_dir: Path | None = None,
    cfr_jar: Path | None = None,
    max_chars: int = 24000,
) -> tuple[str | None, str]:
    """Decompile a single class from its deployed jar.

    Returns ``(source, note)``. ``source`` is ``None`` on failure, with ``note``
    explaining why; on success ``note`` carries the jar path used.
    """
    module = config.class_fqn_to_module(class_fqn)
    if not module:
        return None, (f"{class_fqn} is not a cn.iocoder.yudao.module.* business "
                      "class — nothing to decompile from a module jar")
    cfr = cfr_jar or config.CFR_JAR
    if not Path(cfr).exists():
        return None, (f"CFR jar not found at {cfr}. Download org.benf:cfr and/or "
                      "set OH_FL_CFR")
    jar = find_jar_for_class(class_fqn, jars_dir)
    if jar is None:
        return None, (f"no built jar for module '{module}' under "
                      f"{jars_dir or config.JARS_DIR} (build it with mvn package)")

    # jarfilter is a regex over the class's binary path; match the exact class and
    # its inner classes, tolerating '.' or '/' separators.
    pat = re.escape(class_fqn).replace(r"\.", "[./]") + r"(\$[\w$]+)?"
    cmd = [_java(), "-jar", str(cfr), str(jar), "--jarfilter", pat]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, f"CFR timed out decompiling {class_fqn}"
    src = proc.stdout
    if not src.strip():
        return None, (f"CFR produced no output for {class_fqn} (in {jar.name}); "
                      f"stderr: {proc.stderr[-300:]}")
    if len(src) > max_chars:
        src = src[:max_chars] + f"\n// ... truncated ({len(src)} chars total) ...\n"
    return src, f"decompiled from {jar}"
