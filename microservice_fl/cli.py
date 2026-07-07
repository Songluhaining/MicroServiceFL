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


@app.command("collect")
def collect_cmd(
    metric: bool = typer.Option(False, "--metric", help="collect psutil cpu/mem -> metric.csv"),
    log: bool = typer.Option(False, "--log", help="tail service logs -> log.csv"),
    interval: int = typer.Option(None, "--interval", help="sample interval seconds"),
) -> None:
    """Run the real-time metric/log collectors (feed the live CSVs; Ctrl-C stops).

    With neither flag, runs both. Point OH_FL_METRIC_CSV / OH_FL_LOG_CSV /
    OH_FL_YUDAO_LOG_DIR at your paths first. Typically run under nohup.
    """
    import threading
    import time

    from microservice_fl.collectors import discover_pids, run_logs, run_metrics
    from microservice_fl.collectors.core import run_retention

    if not metric and not log:
        metric = log = True
    pids = discover_pids()
    typer.echo(f"discovered {len(pids)} service PIDs: {sorted(pids)}")
    if not pids:
        _warn("no service PIDs found (is `jps` on PATH and are the services running?)")

    stop = threading.Event()
    threads = []
    if metric:
        threads.append(threading.Thread(target=run_metrics, args=(interval, stop), daemon=True))
    if log:
        threads.append(threading.Thread(target=run_logs, args=(interval, stop), daemon=True))
    # prune old rows so the live CSVs stay bounded (OH_FL_RETENTION_HOURS)
    threads.append(threading.Thread(target=run_retention, args=(None, 3600, stop), daemon=True))
    for t in threads:
        t.start()
    typer.echo(f"retention: keeping last {config.RETENTION_HOURS}h of metric/log CSV")
    typer.echo(f"collecting (metric={metric} log={log}) -> {config.METRIC_CSV} / {config.LOG_CSV}")
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        typer.echo("\nstopped")


@app.command("watch")
def watch_cmd(
    interval: int = typer.Option(60, "--interval", help="seconds between detection ticks"),
    window: int = typer.Option(180, "--window", help="detection sample window (s)"),
    locate_window: int = typer.Option(300, "--locate-window", help="window handed to /locate (s)"),
    cooldown: int = typer.Option(600, "--cooldown", help="min seconds between localizations"),
    k: float = typer.Option(3.0, "--k", help="distribution threshold in sigmas"),
    warmup: int = typer.Option(15, "--warmup", help="samples to learn normal before detecting"),
    out_dir: str = typer.Option(None, "--out", help="incident reports dir (default ./incidents)"),
    once: bool = typer.Option(False, "--once", help="run a single detection tick and exit"),
) -> None:
    """Autonomously monitor metrics; statistically detect anomalies and auto-localize.

    Metrics (cpu/mem/latency/error-count) drive a rolling statistical detector;
    on a breach it runs /locate for the anomalous service and writes a report.
    Typically run under nohup alongside `fl collect`.
    """
    from microservice_fl.monitor.watch import watch

    watch(interval=interval, window_sec=window, locate_window_sec=locate_window,
          cooldown=cooldown, k=k, warmup=warmup, out_dir=out_dir, once=once,
          log=lambda m: typer.echo(m))


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
