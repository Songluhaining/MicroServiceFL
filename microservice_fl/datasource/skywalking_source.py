"""Live :class:`DataSource` backed by SkyWalking OAP (+ live CSV for metric/log).

This is the production, no-ingest, never-stale counterpart to
:class:`DuckDBDataSource`. It routes each modality to where it actually lives in
the user's stack (SkyWalking Java Agent + OAP for trace; psutil/log collectors
writing CSV for metric/log):

* **trace** signals (endpoint anomalies, topology, error spans, endpoint
  breakdown) come from OAP's GraphQL API, queried for the incident window on
  demand — nothing to ingest, never stale;
* **metric** (cpu/mem) and **log** come straight from the continuously-appended
  CSV files (``OH_FL_METRIC_CSV`` / ``OH_FL_LOG_CSV``), window-filtered via a
  transient DuckDB ``read_csv`` (no persistent database).

No tool or skill code changes — this satisfies the same :class:`DataSource`
surface, so ``/locate`` works identically whether offline or live.

GraphQL version note: SkyWalking's schema shifts between major versions. The
queries below target 9.x/10.x (``queryBasicTraces`` / ``queryTrace`` /
``getGlobalTopology``, which are the most stable). If your OAP rejects a query,
adjust the strings in ``_Q`` — they are all centralized there. Service/endpoint
anomaly ranking is derived by *sampling* traces in the window (bounded by
``page_size``); for very high-traffic services raise ``page_size`` or back these
two methods with OAP's ``readMetricsValues`` metrics API.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from microservice_fl import config
from microservice_fl.datasource.base import (
    Case,
    DataSource,
    EndpointStat,
    ErrorSignal,
    LogEntry,
    OperationStat,
    ServiceStat,
    TimeWindow,
    TopologyEdge,
)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FMT)


def _sw_time(dt: datetime) -> str:
    """SkyWalking Duration string at MINUTE step: ``yyyy-MM-dd HHmm``."""
    return dt.strftime("%Y-%m-%d %H%M")


#: Centralized GraphQL queries (verify names against your OAP version).
_Q = {
    "basic_traces": """
      query ($c: TraceQueryCondition) {
        data: queryBasicTraces(condition: $c) {
          traces { key: segmentId endpointNames duration start isError traceIds }
        }
      }""",
    "trace": """
      query ($id: ID!) {
        trace: queryTrace(traceId: $id) {
          spans {
            endpointName serviceCode type peer component isError layer
            startTime endTime
            tags { key value }
          }
        }
      }""",
    "topology": """
      query ($d: Duration!) {
        topo: getGlobalTopology(duration: $d) {
          nodes { id name }
          calls { source target detectPoints }
        }
      }""",
}


class SkyWalkingDataSource(DataSource):
    """Query SkyWalking OAP live; read metric/log from live CSV."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        metric_csv: str | None = None,
        log_csv: str | None = None,
        tz_offset_hours: float | None = None,
        page_size: int = 400,
        timeout: float = 30.0,
    ) -> None:
        self._url = base_url or config.SKYWALKING_URL
        self._metric_csv = metric_csv if metric_csv is not None else config.METRIC_CSV
        self._log_csv = log_csv if log_csv is not None else config.LOG_CSV
        self._tz_offset = (
            tz_offset_hours if tz_offset_hours is not None else config.SKYWALKING_TZ_OFFSET
        )
        self._page = page_size
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # -- GraphQL plumbing --------------------------------------------------- #

    def _gql(self, query: str, variables: dict) -> dict:
        resp = self._client.post(self._url, json={"query": query, "variables": variables})
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"SkyWalking GraphQL error: {payload['errors']}")
        return payload.get("data", {})

    def _duration(self, start: datetime, end: datetime) -> dict:
        # OAP interprets Duration in UTC; shift the (possibly local) window back.
        off = timedelta(hours=self._tz_offset)
        return {"start": _sw_time(start - off), "end": _sw_time(end - off), "step": "MINUTE"}

    def _resolve(self, window: TimeWindow) -> tuple[datetime, datetime, datetime, datetime]:
        start, end = _parse(window.start), _parse(window.end)
        if window.baseline_start and window.baseline_end:
            return start, end, _parse(window.baseline_start), _parse(window.baseline_end)
        duration = max(end - start, timedelta(seconds=1))
        return start, end, start - duration, start

    def _basic_traces(
        self, start: datetime, end: datetime, *, service_id: str | None = None,
        state: str = "ALL",
    ) -> list[dict]:
        cond = {
            "queryDuration": self._duration(start, end),
            "traceState": state,
            "queryOrder": "BY_DURATION",
            "paging": {"pageNum": 1, "pageSize": self._page},
        }
        if service_id:
            cond["serviceId"] = service_id
        data = self._gql(_Q["basic_traces"], {"c": cond})
        return (data.get("data") or {}).get("traces") or []

    def _trace_spans(self, trace_id: str) -> list[dict]:
        data = self._gql(_Q["trace"], {"id": trace_id})
        return (data.get("trace") or {}).get("spans") or []

    @staticmethod
    def _first_trace_id(t: dict) -> str | None:
        ids = t.get("traceIds") or []
        return ids[0] if ids else None

    @staticmethod
    def _exception_type(span: dict) -> str:
        for tag in span.get("tags") or []:
            if tag.get("key") in ("error.kind", "exception.type", "status_code"):
                return str(tag.get("value") or "unknown")
        return "unknown"

    # -- localization signals (trace, from OAP) ----------------------------- #

    def endpoint_anomalies(
        self, service: str, window: TimeWindow, *, top_n: int = 20
    ) -> list[EndpointStat]:
        start, end, b_start, b_end = self._resolve(window)

        def agg(s: datetime, e: datetime) -> dict[str, list[float]]:
            by_ep: dict[str, list[float]] = defaultdict(list)
            errs: dict[str, int] = defaultdict(int)
            for t in self._basic_traces(s, e):
                names = t.get("endpointNames") or []
                ep = names[0] if names else "(unknown)"
                by_ep[ep].append(float(t.get("duration") or 0))
                if t.get("isError"):
                    errs[ep] += 1
            return {ep: (vals, errs[ep]) for ep, vals in by_ep.items()}  # type: ignore[misc]

        cur = agg(start, end)
        base = agg(b_start, b_end)
        out: list[EndpointStat] = []
        for ep, (vals, err) in cur.items():
            cnt = len(vals)
            avg = sum(vals) / cnt if cnt else 0.0
            p95 = sorted(vals)[int(0.95 * (cnt - 1))] if cnt else 0.0
            p99 = sorted(vals)[int(0.99 * (cnt - 1))] if cnt else 0.0
            b_vals = base.get(ep, ([], 0))[0]
            b_avg = sum(b_vals) / len(b_vals) if b_vals else 0.0
            out.append(
                EndpointStat(
                    service=service, endpoint=ep, span_type="Entry", count=cnt,
                    error_count=err, error_rate=err / cnt if cnt else 0.0,
                    avg_latency_ms=avg, p95_latency_ms=p95, p99_latency_ms=p99,
                    max_latency_ms=max(vals) if vals else 0.0,
                    baseline_avg_latency_ms=b_avg, baseline_p95_latency_ms=0.0,
                )
            )
        out.sort(key=lambda s: (s.error_rate, s.avg_latency_ms - s.baseline_avg_latency_ms),
                 reverse=True)
        return out[:top_n]

    def service_anomalies(self, window: TimeWindow, *, top_n: int = 20) -> list[ServiceStat]:
        start, end, b_start, b_end = self._resolve(window)

        def agg(s: datetime, e: datetime) -> dict[str, tuple[list[float], int]]:
            by_svc: dict[str, list[float]] = defaultdict(list)
            errs: dict[str, int] = defaultdict(int)
            for t in self._basic_traces(s, e):
                tid = self._first_trace_id(t)
                if not tid:
                    continue
                spans = self._trace_spans(tid)
                entry = next((sp for sp in spans if sp.get("type") == "Entry"), None)
                svc = (entry or {}).get("serviceCode") or "(unknown)"
                by_svc[svc].append(float(t.get("duration") or 0))
                if t.get("isError"):
                    errs[svc] += 1
            return {svc: (vals, errs[svc]) for svc, vals in by_svc.items()}

        cur = agg(start, end)
        base = agg(b_start, b_end)
        cpu_mem = self._service_cpu_mem(start, end)
        out: list[ServiceStat] = []
        for svc, (vals, err) in cur.items():
            cnt = len(vals)
            avg = sum(vals) / cnt if cnt else 0.0
            p95 = sorted(vals)[int(0.95 * (cnt - 1))] if cnt else 0.0
            b_vals, b_err = base.get(svc, ([], 0))
            b_cnt = len(b_vals)
            cpu, mem = cpu_mem.get(svc, (None, None))
            out.append(
                ServiceStat(
                    service=svc, error_count=err, span_count=cnt,
                    error_rate=err / cnt if cnt else 0.0, avg_latency_ms=avg,
                    p95_latency_ms=p95,
                    baseline_avg_latency_ms=(sum(b_vals) / b_cnt if b_cnt else 0.0),
                    baseline_error_rate=(b_err / b_cnt if b_cnt else 0.0),
                    cpu_pct=cpu, mem_pct=mem,
                )
            )
        out.sort(key=lambda s: (s.error_rate - s.baseline_error_rate,
                                s.avg_latency_ms - s.baseline_avg_latency_ms), reverse=True)
        return out[:top_n]

    def topology(self, window: TimeWindow, *, service: str | None = None) -> list[TopologyEdge]:
        start, end, _, _ = self._resolve(window)
        data = self._gql(_Q["topology"], {"d": self._duration(start, end)})
        topo = data.get("topo") or {}
        name_by_id = {n["id"]: n["name"] for n in topo.get("nodes") or []}
        edges: list[TopologyEdge] = []
        for c in topo.get("calls") or []:
            caller = name_by_id.get(c.get("source"), c.get("source"))
            callee = name_by_id.get(c.get("target"), c.get("target"))
            if service and caller != service:
                continue
            edges.append(
                TopologyEdge(caller=caller, callee=callee, call_count=0,
                             error_count=0, error_rate=0.0, avg_latency_ms=0.0,
                             p95_latency_ms=0.0)
            )
        return edges

    def span_errors(
        self, service: str | None, window: TimeWindow, *, top_n: int = 20
    ) -> list[ErrorSignal]:
        start, end, _, _ = self._resolve(window)
        agg: dict[tuple[str, str, str], int] = defaultdict(int)
        sample: dict[tuple[str, str, str], str | None] = {}
        for t in self._basic_traces(start, end, state="ERROR"):
            tid = self._first_trace_id(t)
            if not tid:
                continue
            for sp in self._trace_spans(tid):
                if not sp.get("isError"):
                    continue
                svc = sp.get("serviceCode") or "(unknown)"
                if service and svc != service:
                    continue
                key = (svc, sp.get("endpointName") or "", self._exception_type(sp))
                agg[key] += 1
                sample.setdefault(key, self._span_stack(sp))
        rows = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        return [
            ErrorSignal(service=svc, endpoint=ep, error_type=etype, count=n,
                        sample_stack=sample.get((svc, ep, etype)))
            for (svc, ep, etype), n in rows
        ]

    @staticmethod
    def _span_stack(span: dict) -> str | None:
        for tag in span.get("tags") or []:
            if tag.get("key") in ("error.stack", "stacktrace", "exception.stacktrace"):
                return str(tag.get("value"))
        return None

    def endpoint_breakdown(
        self, service: str, endpoint: str, window: TimeWindow, *, top_n: int = 15
    ) -> list[OperationStat]:
        start, end, _, _ = self._resolve(window)
        agg: dict[tuple[str, str], list[float]] = defaultdict(list)
        errs: dict[tuple[str, str], int] = defaultdict(int)
        for t in self._basic_traces(start, end):
            names = t.get("endpointNames") or []
            if endpoint not in names and not any(endpoint in n for n in names):
                continue
            tid = self._first_trace_id(t)
            if not tid:
                continue
            for sp in self._trace_spans(tid):
                if sp.get("type") == "Entry":
                    continue
                comp = sp.get("component") or "(local)"
                op = sp.get("endpointName") or comp
                dur = float((sp.get("endTime") or 0) - (sp.get("startTime") or 0))
                agg[(comp, op)].append(dur)
                if sp.get("isError"):
                    errs[(comp, op)] += 1
        ops = [
            OperationStat(
                component=comp, operation=op, count=len(vals), error_count=errs[(comp, op)],
                avg_latency_ms=sum(vals) / len(vals) if vals else 0.0,
                total_latency_ms=sum(vals), max_latency_ms=max(vals) if vals else 0.0,
            )
            for (comp, op), vals in agg.items()
        ]
        ops.sort(key=lambda o: o.total_latency_ms, reverse=True)
        return ops[:top_n]

    # -- metric / log (live CSV, no ingest) --------------------------------- #

    def _csv_conn(self):
        import duckdb

        return duckdb.connect(":memory:")

    def _service_cpu_mem(
        self, start: datetime, end: datetime
    ) -> dict[str, tuple[float | None, float | None]]:
        if not self._metric_csv or not Path(self._metric_csv).exists():
            return {}
        con = self._csv_conn()
        try:
            rows = con.execute(
                "SELECT service, avg(TRY_CAST(proc_cpu_pct AS DOUBLE)), "
                "avg(TRY_CAST(proc_mem_pct AS DOUBLE)) FROM "
                "read_csv_auto(?, types={'timestamp': 'VARCHAR'}, sample_size=-1, "
                "ignore_errors=true) "
                "WHERE level = 'process' "
                "AND strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ') >= ? "
                "AND strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ') < ? GROUP BY service",
                [self._metric_csv, start, end],
            ).fetchall()
        except Exception:
            return {}
        finally:
            con.close()
        return {r[0]: (r[1], r[2]) for r in rows}

    def error_logs(
        self, service: str | None, window: TimeWindow, *,
        levels: tuple[str, ...] = ("ERROR", "EXCEPTION"), pattern: str | None = None,
        limit: int = 50,
    ) -> list[LogEntry]:
        if not self._log_csv or not Path(self._log_csv).exists():
            return []  # logs not centralized/available -> graceful empty
        start, end, _, _ = self._resolve(window)
        con = self._csv_conn()
        sql = (
            "SELECT timestamp, service, level, message FROM "
            "read_csv_auto(?, types={'timestamp': 'VARCHAR'}, sample_size=-1, "
            "ignore_errors=true) "
            "WHERE strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ') >= ? "
            "AND strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ') < ? "
        )
        params: list[object] = [self._log_csv, start, end]
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
        sql += "ORDER BY timestamp LIMIT ?"
        params.append(limit)
        try:
            rows = con.execute(sql, params).fetchall()
        except Exception:
            return []
        finally:
            con.close()
        return [
            LogEntry(timestamp=ts, service=svc, level=lvl, message=msg)
            for ts, svc, lvl, msg in rows
        ]

    # -- ground truth (N/A live) -------------------------------------------- #

    def list_cases(self) -> list[Case]:
        return []

    def get_case(self, case_id: str) -> Case | None:
        del case_id
        return None
