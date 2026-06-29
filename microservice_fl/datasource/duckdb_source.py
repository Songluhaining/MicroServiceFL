"""DuckDB-backed :class:`DataSource` over the ingested dataset.

All window queries compare a *fault window* against a *baseline window* (the
equal-length quiet period immediately before it, unless the caller supplies an
explicit baseline) so the tools surface anomaly *lift* rather than raw values.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from microservice_fl import config
from microservice_fl.datasource.base import (
    Case,
    DataSource,
    EndpointStat,
    ErrorSignal,
    LogEntry,
    ServiceStat,
    TimeWindow,
    TopologyEdge,
)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
#: Boolean-coercing SQL fragment for the trace ``is_error`` column.
_ERR = "CASE WHEN TRY_CAST(is_error AS BOOLEAN) THEN 1 ELSE 0 END"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FMT)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


class DuckDBDataSource(DataSource):
    """Read-only DuckDB query layer. Safe to construct lazily and reuse."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or config.DB_PATH
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"DuckDB database not found at {self._db_path}. "
                f"Run `python -m microservice_fl.ingest` first."
            )
        self._conn = duckdb.connect(str(self._db_path), read_only=True)

    def close(self) -> None:
        self._conn.close()

    def _columns(self, table: str) -> set[str]:
        """Return the column names of a table (used to support enriched fields)."""
        try:
            rows = self._conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        except duckdb.Error:
            return set()
        return {r[1] for r in rows}

    # -- baseline resolution ------------------------------------------------ #

    def _resolve(self, window: TimeWindow) -> tuple[datetime, datetime, datetime, datetime]:
        start, end = _parse(window.start), _parse(window.end)
        if window.baseline_start and window.baseline_end:
            return start, end, _parse(window.baseline_start), _parse(window.baseline_end)
        duration = max(end - start, timedelta(seconds=1))
        return start, end, start - duration, start

    # -- ground truth ------------------------------------------------------- #

    def list_cases(self) -> list[Case]:
        rows = self._conn.execute(
            "SELECT case_id, fault_start, fault_end, fault_type, service, module, "
            "class_fqn, method, param, trigger_url FROM ground_truth ORDER BY case_id"
        ).fetchall()
        return [Case(*r) for r in rows]

    def get_case(self, case_id: str) -> Case | None:
        row = self._conn.execute(
            "SELECT case_id, fault_start, fault_end, fault_type, service, module, "
            "class_fqn, method, param, trigger_url FROM ground_truth WHERE case_id = ?",
            [case_id],
        ).fetchone()
        return Case(*row) if row else None

    # -- service-level anomalies ------------------------------------------- #

    def service_anomalies(self, window: TimeWindow, *, top_n: int = 20) -> list[ServiceStat]:
        start, end, b_start, b_end = self._resolve(window)

        def svc_trace(s: datetime, e: datetime) -> dict[str, tuple]:
            rows = self._conn.execute(
                f"SELECT service, count(*), sum({_ERR}), avg(span_duration), "
                f"quantile_cont(span_duration, 0.95) "
                f"FROM trace WHERE span_type = 'Entry' AND ts >= ? AND ts < ? "
                f"GROUP BY service",
                [s, e],
            ).fetchall()
            return {r[0]: r for r in rows}

        cur, base = svc_trace(start, end), svc_trace(b_start, b_end)
        metric = {
            r[0]: (r[1], r[2])
            for r in self._conn.execute(
                "SELECT service, avg(proc_cpu_pct), avg(proc_mem_pct) FROM metric "
                "WHERE level = 'process' AND ts >= ? AND ts < ? GROUP BY service",
                [start, end],
            ).fetchall()
        }

        out: list[ServiceStat] = []
        for svc, (_, cnt, errs, avg_lat, p95) in cur.items():
            cnt = int(cnt or 0)
            errs = int(errs or 0)
            b = base.get(svc)
            b_cnt = int(b[1] or 0) if b else 0
            b_errs = int(b[2] or 0) if b else 0
            cpu, mem = metric.get(svc, (None, None))
            out.append(
                ServiceStat(
                    service=svc,
                    error_count=errs,
                    span_count=cnt,
                    error_rate=errs / cnt if cnt else 0.0,
                    avg_latency_ms=float(avg_lat or 0.0),
                    p95_latency_ms=float(p95 or 0.0),
                    baseline_avg_latency_ms=float(b[3] or 0.0) if b else 0.0,
                    baseline_error_rate=(b_errs / b_cnt if b_cnt else 0.0),
                    cpu_pct=float(cpu) if cpu is not None else None,
                    mem_pct=float(mem) if mem is not None else None,
                )
            )

        def score(s: ServiceStat) -> tuple[float, float]:
            err_lift = s.error_rate - s.baseline_error_rate
            lat_lift = s.avg_latency_ms - s.baseline_avg_latency_ms
            return (err_lift, lat_lift)

        out.sort(key=score, reverse=True)
        return out[:top_n]

    # -- endpoint-level anomalies ------------------------------------------ #

    def endpoint_anomalies(
        self, service: str, window: TimeWindow, *, top_n: int = 20
    ) -> list[EndpointStat]:
        start, end, b_start, b_end = self._resolve(window)

        def ep(s: datetime, e: datetime) -> dict[str, tuple]:
            rows = self._conn.execute(
                f"SELECT endpoint, any_value(span_type), count(*), sum({_ERR}), "
                f"avg(span_duration), quantile_cont(span_duration, 0.95), "
                f"quantile_cont(span_duration, 0.99), max(span_duration) "
                f"FROM trace WHERE service = ? AND span_type = 'Entry' "
                f"AND ts >= ? AND ts < ? GROUP BY endpoint",
                [service, s, e],
            ).fetchall()
            return {r[0]: r for r in rows}

        cur, base = ep(start, end), ep(b_start, b_end)
        out: list[EndpointStat] = []
        for endpoint, (_, span_type, cnt, errs, avg_lat, p95, p99, mx) in cur.items():
            cnt = int(cnt or 0)
            errs = int(errs or 0)
            b = base.get(endpoint)
            out.append(
                EndpointStat(
                    service=service,
                    endpoint=endpoint,
                    span_type=span_type or "Entry",
                    count=cnt,
                    error_count=errs,
                    error_rate=errs / cnt if cnt else 0.0,
                    avg_latency_ms=float(avg_lat or 0.0),
                    p95_latency_ms=float(p95 or 0.0),
                    p99_latency_ms=float(p99 or 0.0),
                    max_latency_ms=float(mx or 0.0),
                    baseline_avg_latency_ms=float(b[4] or 0.0) if b else 0.0,
                    baseline_p95_latency_ms=float(b[5] or 0.0) if b else 0.0,
                )
            )
        out.sort(
            key=lambda s: (s.error_rate, s.avg_latency_ms - s.baseline_avg_latency_ms),
            reverse=True,
        )
        return out[:top_n]

    # -- topology ----------------------------------------------------------- #

    def topology(self, window: TimeWindow, *, service: str | None = None) -> list[TopologyEdge]:
        start, end, _, _ = self._resolve(window)
        sql = (
            f"SELECT service, split_part(endpoint, '/', 3) AS callee_module, "
            f"count(*), sum({_ERR}), avg(span_duration), "
            f"quantile_cont(span_duration, 0.95) "
            f"FROM trace WHERE component = 'Feign' AND ts >= ? AND ts < ? "
        )
        params: list[object] = [start, end]
        if service:
            sql += "AND service = ? "
            params.append(service)
        sql += "GROUP BY service, callee_module"

        edges: list[TopologyEdge] = []
        for caller, callee_module, cnt, errs, avg_lat, p95 in self._conn.execute(
            sql, params
        ).fetchall():
            callee = config.module_to_service(callee_module or "")
            if not callee:
                continue
            cnt = int(cnt or 0)
            errs = int(errs or 0)
            edges.append(
                TopologyEdge(
                    caller=caller,
                    callee=callee,
                    call_count=cnt,
                    error_count=errs,
                    error_rate=errs / cnt if cnt else 0.0,
                    avg_latency_ms=float(avg_lat or 0.0),
                    p95_latency_ms=float(p95 or 0.0),
                )
            )
        edges.sort(key=lambda e: (e.error_rate, e.avg_latency_ms), reverse=True)
        return edges

    # -- logs --------------------------------------------------------------- #

    def error_logs(
        self,
        service: str | None,
        window: TimeWindow,
        *,
        levels: tuple[str, ...] = ("ERROR", "EXCEPTION"),
        pattern: str | None = None,
        limit: int = 50,
    ) -> list[LogEntry]:
        start, end, _, _ = self._resolve(window)
        cols = self._columns("log")
        logger_col = "logger" if "logger" in cols else "NULL AS logger"
        stack_col = "stack_trace" if "stack_trace" in cols else "NULL AS stack_trace"

        sql = (
            f"SELECT timestamp, service, level, message, {logger_col}, {stack_col} "
            f"FROM log WHERE ts >= ? AND ts < ? "
        )
        params: list[object] = [start, end]
        if service:
            sql += "AND service = ? "
            params.append(service)
        if levels:
            placeholders = ", ".join("?" for _ in levels)
            sql += f"AND level IN ({placeholders}) "
            params.extend(levels)
        if pattern:
            sql += "AND message ILIKE ? "
            params.append(f"%{pattern}%")
        sql += "ORDER BY ts LIMIT ?"
        params.append(limit)

        return [
            LogEntry(
                timestamp=ts, service=svc, level=lvl, message=msg,
                logger=logger, stack_trace=stack,
            )
            for ts, svc, lvl, msg, logger, stack in self._conn.execute(sql, params).fetchall()
        ]

    def span_errors(
        self, service: str | None, window: TimeWindow, *, top_n: int = 20
    ) -> list[ErrorSignal]:
        start, end, _, _ = self._resolve(window)
        cols = self._columns("trace")
        etype = "error_type" if "error_type" in cols else "'unknown' AS error_type"
        estack = "any_value(error_stack)" if "error_stack" in cols else "NULL"

        sql = (
            f"SELECT service, endpoint, {etype}, count(*) AS n, {estack} AS stack "
            f"FROM trace WHERE ts >= ? AND ts < ? "
            f"AND TRY_CAST(is_error AS BOOLEAN) "
        )
        params: list[object] = [start, end]
        if service:
            sql += "AND service = ? "
            params.append(service)
        # group on whichever error_type expression resolved to
        group_type = "error_type" if "error_type" in cols else "3"
        sql += f"GROUP BY service, endpoint, {group_type} ORDER BY n DESC LIMIT ?"
        params.append(top_n)

        return [
            ErrorSignal(
                service=svc, endpoint=ep, error_type=etype_v or "unknown",
                count=int(n), sample_stack=stack,
            )
            for svc, ep, etype_v, n, stack in self._conn.execute(sql, params).fetchall()
        ]
