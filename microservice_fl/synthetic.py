"""Generate a small, signal-present demo dataset in the canonical schema.

The original reference collection lacked fault signal, which makes it impossible
to validate a localizer on it. This generator fabricates a compact dataset that
*does* contain the injected effects — elevated endpoint latency for ``delay``
faults, error spans + exception logs (with logger FQN and stack) for
``exception`` faults, and downstream-propagated slowness so topology can
distinguish root cause from victim. It writes the exact tables/columns the
``DuckDBDataSource`` queries, so the full agent + evaluation pipeline runs
against it unchanged.

Usage::

    python -m microservice_fl.synthetic --db .../fl_demo.duckdb
    set OH_FL_DB=.../fl_demo.duckdb     # then run the agent / eval against it
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from microservice_fl import config

_TS = "%Y-%m-%dT%H:%M:%SZ"

# A compact catalog: per service, a few (endpoint, class, method) the load hits.
# class names follow the yudao convention so config mappings resolve correctly.
_CATALOG: dict[str, list[tuple[str, str, str]]] = {
    "yudao-system": [
        ("POST:/admin-api/system/mail-account/delete-list",
         "cn.iocoder.yudao.module.system.service.mail.MailAccountServiceImpl",
         "deleteMailAccountList"),
        ("GET:/admin-api/system/user/page",
         "cn.iocoder.yudao.module.system.service.user.AdminUserServiceImpl", "getUserPage"),
    ],
    "yudao-infra": [
        ("GET:/admin-api/infra/file/page",
         "cn.iocoder.yudao.module.infra.service.file.FileServiceImpl", "getFilePage"),
    ],
    "yudao-crm": [
        ("GET:/admin-api/crm/business/page",
         "cn.iocoder.yudao.module.crm.service.business.CrmBusinessServiceImpl",
         "getBusinessPage"),
        ("GET:/admin-api/crm/customer/page",
         "cn.iocoder.yudao.module.crm.service.customer.CrmCustomerServiceImpl",
         "getCustomerPage"),
    ],
    "yudao-erp": [
        ("GET:/admin-api/erp/finance-payment/page",
         "cn.iocoder.yudao.module.erp.service.finance.ErpFinancePaymentServiceImpl",
         "getFinancePaymentPage"),
    ],
    "yudao-bpm": [
        ("GET:/admin-api/bpm/process-listener/get",
         "cn.iocoder.yudao.module.bpm.service.definition.BpmProcessListenerServiceImpl",
         "getProcessListener"),
    ],
    "yudao-member": [
        ("GET:/admin-api/member/experience-record/page",
         "cn.iocoder.yudao.module.member.service.level.MemberExperienceRecordServiceImpl",
         "getExperienceRecordPage"),
    ],
    "yudao-mall-trade": [
        ("GET:/admin-api/trade/after-sale-log/page",
         "cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleLogServiceImpl",
         "getAfterSaleLogList"),
    ],
    "yudao-mall-product": [
        ("GET:/admin-api/product/spu/page",
         "cn.iocoder.yudao.module.product.service.spu.ProductSpuServiceImpl", "getSpuPage"),
    ],
    "yudao-mall-promotion": [
        ("GET:/admin-api/promotion/coupon/page",
         "cn.iocoder.yudao.module.promotion.service.coupon.CouponServiceImpl", "getCouponPage"),
    ],
    "yudao-mall-statistics": [
        ("GET:/admin-api/statistics/trade/summary",
         "cn.iocoder.yudao.module.statistics.service.trade.TradeStatisticsServiceImpl",
         "getTradeSummary"),
    ],
}

# Shared downstreams every business service calls (auth on system, logging on infra).
_DOWNSTREAMS = {
    "yudao-system": "/rpc-api/system/permission/has-any-permissions",
    "yudao-infra": "/rpc-api/infra/api-access-log/create",
}
_PORT = {"yudao-system": "10.29.4.21:48081", "yudao-infra": "10.29.4.21:48082"}
_BASE_LATENCY = 12.0  # ms, normal


def _exc_type(method: str) -> tuple[str, str]:
    """Return (exception fqn, a sample stack) for an exception fault on a method."""
    fqn = "java.lang.NullPointerException"
    return fqn, "\n".join([
        f"{fqn}: Cannot invoke method because the return value is null",
        "\tat {cls}.{m}(Impl.java:142)",
        "\tat jdk.internal.reflect.GeneratedMethodAccessor.invoke(Unknown Source)",
        "\tat org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(...)",
    ]).replace("{m}", method)


class _Builder:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.trace: list[dict] = []
        self.log: list[dict] = []
        self.metric: list[dict] = []
        self.gt: list[dict] = []
        self._span = 0

    def _next_span(self) -> int:
        self._span += 1
        return self._span

    def emit_request(self, t: datetime, caller: str, fault: dict | None) -> None:
        """Emit one request to ``caller`` at time ``t`` (Entry + Feign exits + DB)."""
        endpoint, klass, method = self.rng.choice(_CATALOG[caller])
        trace_id = f"t{self._next_span():08d}"
        entry_latency = _BASE_LATENCY + self.rng.uniform(-3, 6)
        entry_error = False
        err_type = None
        err_stack = None

        # downstream calls (auth + logging) — capture victim propagation
        exits = []
        for ds, ds_ep in _DOWNSTREAMS.items():
            if ds == caller:
                continue  # a service does not Feign-RPC to itself
            ds_lat = _BASE_LATENCY + self.rng.uniform(-3, 5)
            ds_err = False
            if fault and fault["service"] == ds and fault["in_window"]:
                if fault["type"] == "delay":
                    ds_lat += fault["delay_ms"]
                else:
                    ds_err = True
            entry_latency += ds_lat
            entry_error = entry_error or ds_err
            exits.append((ds, ds_ep, ds_lat, ds_err))

        # the faulted endpoint on the caller itself
        if fault and fault["service"] == caller and fault["in_window"]:
            endpoint, klass, method = fault["endpoint"], fault["class"], fault["method"]
            if fault["type"] == "delay":
                entry_latency += fault["delay_ms"]
            else:
                entry_error = True
                err_type, err_stack = _exc_type(method)
                err_stack = err_stack.replace("{cls}", klass)

        ts = t.strftime(_TS)
        sstart = int(t.timestamp() * 1000)
        self.trace.append(dict(
            timestamp=ts, trace_id=trace_id, service=caller, endpoint=endpoint,
            span_id=0, parent_span_id=-1, span_start=sstart,
            span_end=sstart + int(entry_latency), span_duration=int(entry_latency),
            span_type="Entry", peer="", component="Tomcat",
            is_error=entry_error, error_type=err_type, error_stack=err_stack,
        ))
        for ds, ds_ep, ds_lat, ds_err in exits:
            self.trace.append(dict(
                timestamp=ts, trace_id=trace_id, service=caller, endpoint=ds_ep,
                span_id=self._next_span(), parent_span_id=0, span_start=sstart,
                span_end=sstart + int(ds_lat), span_duration=int(ds_lat),
                span_type="Exit", peer=_PORT[ds], component="Feign",
                is_error=ds_err, error_type=None, error_stack=None,
            ))
        # a DB span
        self.trace.append(dict(
            timestamp=ts, trace_id=trace_id, service=caller,
            endpoint="Mysql/JDBC/PreparedStatement/execute", span_id=self._next_span(),
            parent_span_id=0, span_start=sstart, span_end=sstart + 3, span_duration=3,
            span_type="Exit", peer="127.0.0.1:13306", component="mysql-connector-java",
            is_error=False, error_type=None, error_stack=None,
        ))
        # exception log line carrying the class FQN (logger) + stack
        if entry_error and err_type:
            self.log.append(dict(
                timestamp=ts, service=caller, level="EXCEPTION", logger=klass,
                trace_id=trace_id, thread="http-nio-exec",
                message=f"{err_type}: handler dispatch failed",
                stack_trace=err_stack,
            ))

    def emit_metric(self, t: datetime, service: str, fault: dict | None) -> None:
        cpu = 5.0 + self.rng.uniform(-1, 1)
        if fault and fault["service"] == service and fault["in_window"] and fault["type"] == "delay":
            cpu += 8.0  # delay faults often raise CPU from thread pile-up
        self.metric.append(dict(
            timestamp=t.strftime(_TS), service=service, level="process",
            cpu_pct=None, mem_pct=None, proc_cpu_pct=round(cpu, 2),
            proc_mem_pct=round(8.0 + self.rng.uniform(-0.5, 0.5), 2),
        ))


def generate(*, db_path: Path, seed: int = 7, n_cases: int = 12) -> dict[str, int]:
    """Build the demo DuckDB database; return ``{table: row_count}``."""
    b = _Builder(seed)
    services = list(_CATALOG.keys())
    t0 = datetime(2026, 7, 1, 0, 0, 0)
    base_min, fault_min, gap_min = 4, 3, 1
    step = timedelta(seconds=10)

    cursor = t0
    for i in range(n_cases):
        svc = services[i % len(services)]
        ftype = "delay" if i % 2 == 0 else "exception"
        endpoint, klass, method = _CATALOG[svc][0]
        delay_ms = b.rng.choice([1000, 2000, 3000])
        fstart = cursor + timedelta(minutes=base_min)
        fend = fstart + timedelta(minutes=fault_min)
        case_id = f"demo{i + 1:05d}"
        b.gt.append(dict(
            case_id=case_id, fault_start=fstart.strftime(_TS), fault_end=fend.strftime(_TS),
            fault_type=ftype, service=svc, module=config.service_to_module(svc),
            class_fqn=klass, method=method,
            param=(f"delay={delay_ms}ms" if ftype == "delay" else "exception=NullPointerException"),
            trigger_url=endpoint.split(":", 1)[-1],
        ))
        span_end = fend + timedelta(minutes=gap_min)
        t = cursor
        while t < span_end:
            in_window = fstart <= t < fend
            fault = {
                "service": svc, "type": ftype, "delay_ms": delay_ms,
                "endpoint": endpoint, "class": klass, "method": method,
                "in_window": in_window,
            }
            # The faulted service always carries the fault. When the fault is on a
            # shared downstream (system/infra), pass it to *callers* too so their
            # RPC edge to it slows/errors — that is the victim propagation that
            # lets fl_topology separate root cause from victims.
            downstream_fault = in_window and svc in ("yudao-system", "yudao-infra")
            for s in {svc, *b.rng.sample(services, 3)}:
                f = fault if (s == svc or downstream_fault) else None
                b.emit_request(t, s, f)
                b.emit_metric(t, s, f)
            t += step
        cursor = span_end

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = duckdb.connect(str(db_path))
    counts: dict[str, int] = {}
    try:
        for name, rows in [("ground_truth", b.gt), ("trace", b.trace),
                           ("log", b.log), ("metric", b.metric)]:
            df = pd.DataFrame(rows)
            ts_col = "fault_start" if name == "ground_truth" else "timestamp"
            df["ts"] = pd.to_datetime(df[ts_col], format=_TS)
            conn.register("df_tmp", df)
            conn.execute(f"CREATE TABLE {name} AS SELECT * FROM df_tmp")
            conn.unregister("df_tmp")
            counts[name] = len(df)
        conn.execute("CREATE INDEX idx_trace_svc_ts ON trace(service, ts)")
        conn.execute("CREATE INDEX idx_log_svc_ts ON log(service, ts)")
        conn.execute("CREATE INDEX idx_metric_svc_ts ON metric(service, ts)")
    finally:
        conn.close()
    return counts


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate a signal-present demo dataset.")
    p.add_argument("--db", type=Path, default=config.DATASET_DIR / "fl_demo.duckdb")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--cases", type=int, default=12)
    args = p.parse_args(argv)
    counts = generate(db_path=args.db, seed=args.seed, n_cases=args.cases)
    print(f"[synthetic] wrote {sum(counts.values()):,} rows -> {args.db}")
    for k, v in counts.items():
        print(f"  {k:<14} {v:,}")


if __name__ == "__main__":
    main()
