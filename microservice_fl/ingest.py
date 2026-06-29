"""Ingest the raw collected CSVs into a queryable DuckDB database.

The trace CSV alone is ~8.5 GB, so it must never be scanned per query. This
one-off step loads each CSV into a native DuckDB table (columnar, compressed),
parses the ISO-8601 timestamps into a ``ts`` column, and builds indexes on the
columns the RCA tools filter by.

Usage::

    python -m microservice_fl.ingest                 # full ingest, default paths
    python -m microservice_fl.ingest --sample 500000 # quick smoke on a trace subset
    python -m microservice_fl.ingest --tables ground_truth,metric,log
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from microservice_fl import config

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: For each timestamped table: the source column we derive ``ts`` from, plus the
#: ISO columns we pin to VARCHAR so they stay plain strings (no timezone-aware
#: datetimes leaking to Python, which would also drag in a pytz dependency).
_TS_SOURCE: dict[str, str] = {
    "ground_truth": "fault_start",
    "phase_timeline": "phase_start",
    "metric": "timestamp",
    "log": "timestamp",
    "trace": "timestamp",
}
_VARCHAR_TS_COLS: dict[str, tuple[str, ...]] = {
    "ground_truth": ("fault_start", "fault_end"),
    "phase_timeline": ("phase_start", "phase_end"),
    "metric": ("timestamp",),
    "log": ("timestamp",),
    "trace": ("timestamp",),
}

#: Per-table index definitions, created after load.
_INDEXES: dict[str, list[str]] = {
    "metric": ["CREATE INDEX IF NOT EXISTS idx_metric_svc_ts ON metric(service, ts)"],
    "log": ["CREATE INDEX IF NOT EXISTS idx_log_svc_ts ON log(service, ts)"],
    "trace": [
        "CREATE INDEX IF NOT EXISTS idx_trace_svc_ts ON trace(service, ts)",
        "CREATE INDEX IF NOT EXISTS idx_trace_type ON trace(span_type)",
        "CREATE INDEX IF NOT EXISTS idx_trace_component ON trace(component)",
    ],
}


def _load_table(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    csv_path: Path,
    *,
    sample: int | None,
) -> int:
    """Load one CSV into a table, adding a parsed ``ts`` column where relevant."""
    if not csv_path.exists():
        raise FileNotFoundError(f"missing CSV for table '{name}': {csv_path}")

    src = csv_path.as_posix()
    limit = f"LIMIT {sample}" if (sample and name == "trace") else ""

    # Pin ISO timestamp columns to VARCHAR so they stay plain strings; everything
    # else is auto-typed. We derive a naive ``ts`` column via strptime for fast,
    # timezone-stable window filtering.
    type_override = ""
    if name in _VARCHAR_TS_COLS:
        cols = ", ".join(f"'{c}': 'VARCHAR'" for c in _VARCHAR_TS_COLS[name])
        type_override = f", types={{{cols}}}"
    reader = (
        f"read_csv_auto('{src}', header=true, ignore_errors=true, "
        f"sample_size=200000{type_override})"
    )

    conn.execute(f"DROP TABLE IF EXISTS {name}")
    if name in _TS_SOURCE:
        ts_col = _TS_SOURCE[name]
        conn.execute(
            f"CREATE TABLE {name} AS SELECT *, "
            f"strptime({ts_col}, '{TS_FORMAT}') AS ts "
            f"FROM {reader} {limit}"
        )
    else:
        conn.execute(f"CREATE TABLE {name} AS SELECT * FROM {reader} {limit}")

    for stmt in _INDEXES.get(name, []):
        conn.execute(stmt)

    return int(conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0])


def ingest(
    *,
    db_path: Path | None = None,
    dataset_dir: Path | None = None,
    tables: list[str] | None = None,
    sample: int | None = None,
    threads: int | None = None,
    memory_limit: str | None = None,
) -> dict[str, int]:
    """Build the DuckDB database; return a ``{table: row_count}`` summary."""
    db_path = db_path or config.DB_PATH
    dataset_dir = dataset_dir or config.DATASET_DIR
    wanted = tables or list(config.CSV_FILES.keys())

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    counts: dict[str, int] = {}
    try:
        if threads:
            conn.execute(f"PRAGMA threads={threads}")
        if memory_limit:
            conn.execute(f"PRAGMA memory_limit='{memory_limit}'")

        for name in wanted:
            if name not in config.CSV_FILES:
                raise ValueError(f"unknown table '{name}'")
            csv_path = dataset_dir / config.CSV_FILES[name]
            t0 = time.time()
            print(f"[ingest] loading {name:<14} <- {csv_path.name} ...", flush=True)
            rows = _load_table(conn, name, csv_path, sample=sample)
            counts[name] = rows
            print(f"[ingest]   {rows:>12,} rows  ({time.time() - t0:.1f}s)", flush=True)
    finally:
        conn.close()
    return counts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest raw CSVs into DuckDB.")
    p.add_argument("--db", type=Path, default=None, help="output DuckDB path")
    p.add_argument("--dataset", type=Path, default=None, help="raw CSV directory")
    p.add_argument("--tables", type=str, default=None, help="comma-separated subset")
    p.add_argument("--sample", type=int, default=None, help="row cap for trace (smoke)")
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--memory-limit", type=str, default=None, help="e.g. 4GB")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    counts = ingest(
        db_path=args.db,
        dataset_dir=args.dataset,
        tables=tables,
        sample=args.sample,
        threads=args.threads,
        memory_limit=args.memory_limit,
    )
    total = sum(counts.values())
    print(f"[ingest] done: {total:,} rows across {len(counts)} tables "
          f"-> {args.db or config.DB_PATH}")


if __name__ == "__main__":
    main()
