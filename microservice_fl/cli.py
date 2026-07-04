"""Unified ``fl`` command line for MicroServiceFL.

One entry point for onboarding a target system and running the localizer:

    fl doctor                       # check the environment
    fl targets                      # list target-system profiles
    fl build-index --jars <dir>     # jars -> endpoint_index.json (localization)
    fl ingest --dataset <dir>       # collected CSVs -> fl.duckdb (offline)
    fl init --jars <dir> --data <dir>   # both, in one step
    fl repl                         # interactive localization (watch each step)
    fl locate "time=... symptom=..."    # one-shot localization

Runs as ``python -m microservice_fl`` or, after ``pip install -e .[fl]``, ``fl``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from microservice_fl import config

app = typer.Typer(
    name="fl",
    help="MicroServiceFL — fine-grained microservice fault localization.",
    no_args_is_help=True,
    add_completion=False,
)


def _ok(msg: str) -> None:
    typer.echo(f"  [ok]   {msg}")


def _warn(msg: str) -> None:
    typer.secho(f"  [warn] {msg}", fg=typer.colors.YELLOW)


def _bad(msg: str) -> None:
    typer.secho(f"  [MISS] {msg}", fg=typer.colors.RED)


@app.command("build-index")
def build_index_cmd(
    jars: Path = typer.Option(..., "--jars", help="dir containing built *-server jars"),
    out: Path = typer.Option(None, "--out", help="endpoint_index.json output path"),
) -> None:
    """Scan the deployed jars' annotations into the grey-box endpoint index."""
    from microservice_fl.greybox.build_index import build

    out = out or config.INDEX_PATH
    index = build(jars, out)
    typer.echo(f"indexed {len(index)} endpoints -> {out}")


@app.command("ingest")
def ingest_cmd(
    dataset: Path = typer.Option(None, "--dataset", help="raw CSV directory"),
    db: Path = typer.Option(None, "--db", help="output DuckDB path"),
    sample: int = typer.Option(None, "--sample", help="row cap for trace (smoke test)"),
) -> None:
    """Build the offline DuckDB from collected CSVs (offline data source only)."""
    from microservice_fl.ingest import ingest

    counts = ingest(db_path=db, dataset_dir=dataset, sample=sample)
    total = sum(counts.values())
    typer.echo(f"ingested {total:,} rows across {len(counts)} tables -> {db or config.DB_PATH}")


@app.command("init")
def init_cmd(
    jars: Path = typer.Option(..., "--jars", help="dir containing built *-server jars"),
    data: Path = typer.Option(None, "--data", help="raw CSV dir (offline mode)"),
    index: Path = typer.Option(None, "--index", help="endpoint_index.json output"),
    db: Path = typer.Option(None, "--db", help="DuckDB output (offline mode)"),
) -> None:
    """One-shot onboarding: build the endpoint index, and the DuckDB if --data given."""
    from microservice_fl.greybox.build_index import build

    idx = build(jars, index or config.INDEX_PATH)
    typer.echo(f"index: {len(idx)} endpoints -> {index or config.INDEX_PATH}")
    if data or config.DATASOURCE == "duckdb":
        from microservice_fl.ingest import ingest

        counts = ingest(db_path=db, dataset_dir=data)
        typer.echo(f"duckdb: {sum(counts.values()):,} rows -> {db or config.DB_PATH}")
    else:
        typer.echo("skipped ingest (live datasource); set --data to build a DuckDB too")


@app.command("targets")
def targets_cmd() -> None:
    """List target-system profiles and show the active one."""
    from microservice_fl.target import available_targets, load_target

    active = load_target()
    typer.echo(f"active target: {active.name}  (jar: {active.jar_template})")
    typer.echo("available:")
    for name in available_targets():
        typer.echo(f"  - {name}{'  *' if name == active.name else ''}")
    typer.echo(f"\nservices in '{active.name}': {len(active.module_by_service)}")


@app.command("doctor")
def doctor_cmd() -> None:
    """Check the environment for build + run readiness."""
    typer.echo("Java (build-index / decompile):")
    for tool in ("java", "javac"):
        (_ok if shutil.which(tool) or _java_home_has(tool) else _bad)(tool)
    typer.echo("Maven (only to build jars from source):")
    (_ok if shutil.which("mvn") else _warn)("mvn")
    typer.echo("Decompiler:")
    (_ok if Path(config.CFR_JAR).exists() else _warn)(f"CFR jar: {config.CFR_JAR}")

    typer.echo(f"Target profile: {config.active_target().name}")
    typer.echo("Grey-box artifacts:")
    (_ok if Path(config.INDEX_PATH).exists() else _warn)(f"endpoint index: {config.INDEX_PATH}")
    (_ok if Path(config.JARS_DIR).exists() else _warn)(f"jars dir: {config.JARS_DIR}")

    typer.echo(f"Data source: {config.DATASOURCE}")
    if config.DATASOURCE == "skywalking":
        (_ok if _http_ok(config.SKYWALKING_URL) else _warn)(f"OAP: {config.SKYWALKING_URL}")
        (_ok if Path(config.LOG_CSV).exists() else _warn)(f"log csv: {config.LOG_CSV}")
        (_ok if Path(config.METRIC_CSV).exists() else _warn)(f"metric csv: {config.METRIC_CSV}")
    else:
        (_ok if Path(config.DB_PATH).exists() else _bad)(f"duckdb: {config.DB_PATH}")

    typer.echo("Model provider (for running /locate):")
    (_ok if _provider_ready() else _warn)("provider auth")


@app.command("repl")
def repl_cmd() -> None:
    """Launch the interactive localizer (prints each tool call; no Node needed)."""
    from microservice_fl.repl import main as repl_main

    repl_main()


@app.command("locate")
def locate_cmd(
    incident: str = typer.Argument(..., help="e.g. 'time=<start>~<end> symptom=<endpoint>'"),
) -> None:
    """One-shot localization: run /locate headless and print the result."""
    prompt = incident if incident.strip().startswith("/locate") else f"/locate {incident}"
    proc = subprocess.run(
        [sys.executable, "-m", "openharness", "-p", prompt,
         "--output-format", "text", "--permission-mode", "auto"],
        env=os.environ.copy(),
    )
    raise typer.Exit(proc.returncode)


# --------------------------------------------------------------------------- #
# small helpers for doctor
# --------------------------------------------------------------------------- #

def _java_home_has(tool: str) -> bool:
    jhome = os.environ.get("JAVA_HOME")
    if not jhome:
        return False
    exe = tool + (".exe" if os.name == "nt" else "")
    return (Path(jhome) / "bin" / exe).exists()


def _http_ok(url: str) -> bool:
    try:
        import httpx

        httpx.post(url, json={"query": "{ version }"}, timeout=5.0)
        return True
    except Exception:
        return False


def _provider_ready() -> bool:
    try:
        from openharness.config.settings import load_settings

        return load_settings().resolve_auth().state == "configured"
    except Exception:
        return False


def main() -> None:
    app()


if __name__ == "__main__":
    main()
