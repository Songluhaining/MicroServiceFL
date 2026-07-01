"""Observability-signal tools: service/endpoint anomalies, topology, errors, logs."""

from __future__ import annotations

from pydantic import Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl.tools._shared import WindowInput, get_source, lift


def _missing_db_result(exc: FileNotFoundError) -> ToolResult:
    return ToolResult(
        output=(
            f"{exc}\n\nThe fault-localization database is not built yet. Run:\n"
            "  python -m microservice_fl.ingest        # from collected CSVs\n"
            "  python -m microservice_fl.synthetic      # or a signal-present demo set"
        ),
        is_error=True,
    )


class ScanServicesInput(WindowInput):
    top_n: int = Field(default=10, ge=1, le=50)


class ScanServicesTool(BaseTool):
    name = "fl_scan_services"
    description = (
        "Rank services by anomaly over a fault window vs baseline: error-rate lift, "
        "latency lift, CPU/mem. First step — narrows the fault to a small set of "
        "candidate services. Returns aggregates, never raw spans."
    )
    input_model = ScanServicesInput

    def is_read_only(self, arguments: ScanServicesInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ScanServicesInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            stats = get_source().service_anomalies(arguments.to_window(), top_n=arguments.top_n)
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not stats:
            return ToolResult(output="No service traffic in the given window.")
        lines = ["service | err_rate (base) | avg_latency lift | cpu% | mem%"]
        for s in stats:
            lines.append(
                f"{s.service} | {s.error_rate:.3f} (b {s.baseline_error_rate:.3f}) | "
                f"{lift(s.avg_latency_ms, s.baseline_avg_latency_ms)} | "
                f"{s.cpu_pct if s.cpu_pct is not None else '-'} | "
                f"{s.mem_pct if s.mem_pct is not None else '-'}"
            )
        return ToolResult(output="\n".join(lines))


class TopologyInput(WindowInput):
    service: str | None = Field(default=None, description="Optional: only edges from this caller")


class TopologyTool(BaseTool):
    name = "fl_topology"
    description = (
        "Service call graph (caller→callee) from Feign RPC spans over the window, "
        "with per-edge latency and error rate. Use to separate the ROOT-CAUSE "
        "service from VICTIM services: if a service is slow only because a "
        "downstream edge is slow, the downstream is the root."
    )
    input_model = TopologyInput

    def is_read_only(self, arguments: TopologyInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TopologyInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            edges = get_source().topology(arguments.to_window(), service=arguments.service)
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not edges:
            return ToolResult(output="No RPC edges in the given window.")
        lines = ["caller → callee | calls | err_rate | avg_latency_ms | p95_ms"]
        for e in edges:
            lines.append(
                f"{e.caller} → {e.callee} | {e.call_count} | {e.error_rate:.3f} | "
                f"{e.avg_latency_ms:.0f} | {e.p95_latency_ms:.0f}"
            )
        return ToolResult(output="\n".join(lines))


class EndpointAnomalyInput(WindowInput):
    service: str = Field(description="Service to drill into, e.g. yudao-crm")
    top_n: int = Field(default=10, ge=1, le=50)


class EndpointAnomalyTool(BaseTool):
    name = "fl_endpoint_anomaly"
    description = (
        "Within one service, rank Entry endpoints (e.g. GET:/admin-api/...) by "
        "latency/error lift vs baseline. The top endpoint maps via the yudao "
        "source to the faulted controller→service class/method (use fl_map_endpoint)."
    )
    input_model = EndpointAnomalyInput

    def is_read_only(self, arguments: EndpointAnomalyInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: EndpointAnomalyInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            stats = get_source().endpoint_anomalies(
                arguments.service, arguments.to_window(), top_n=arguments.top_n
            )
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not stats:
            return ToolResult(output=f"No Entry endpoints for {arguments.service} in window.")
        lines = ["endpoint | count | err_rate | avg_latency lift | p99_ms | max_ms"]
        for e in stats:
            lines.append(
                f"{e.endpoint} | {e.count} | {e.error_rate:.3f} | "
                f"{lift(e.avg_latency_ms, e.baseline_avg_latency_ms)} | "
                f"{e.p99_latency_ms:.0f} | {e.max_latency_ms:.0f}"
            )
        return ToolResult(output="\n".join(lines))


class SpanErrorsInput(WindowInput):
    service: str | None = Field(default=None, description="Optional service filter")
    top_n: int = Field(default=15, ge=1, le=50)


class SpanErrorsTool(BaseTool):
    name = "fl_span_errors"
    description = (
        "Aggregate trace error spans by (service, endpoint, exception type) over the "
        "window, with a sample stack. The top stack frame is the direct bridge to the "
        "throwing class/method — the primary signal for exception-type faults."
    )
    input_model = SpanErrorsInput

    def is_read_only(self, arguments: SpanErrorsInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: SpanErrorsInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            errs = get_source().span_errors(
                arguments.service, arguments.to_window(), top_n=arguments.top_n
            )
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not errs:
            return ToolResult(output="No error spans in the given window.")
        blocks = []
        for e in errs:
            block = f"[{e.count}x] {e.service} {e.endpoint}\n  type: {e.error_type}"
            if e.sample_stack:
                top = e.sample_stack.strip().splitlines()[:4]
                block += "\n  stack:\n    " + "\n    ".join(top)
            blocks.append(block)
        return ToolResult(output="\n".join(blocks))


class EndpointBreakdownInput(WindowInput):
    service: str = Field(description="Root service, e.g. yudao-system")
    endpoint: str = Field(
        description="The slow Entry endpoint, e.g. POST:/admin-api/system/mail-account/delete-list"
    )
    top_n: int = Field(default=12, ge=1, le=50)


class EndpointBreakdownTool(BaseTool):
    name = "fl_endpoint_breakdown"
    description = (
        "Break a slow Entry endpoint into its downstream operations (DB / Feign / "
        "Redis / local spans) over the window, ranked by total time. Names the "
        "dominant slow or erroring leaf — e.g. a specific SQL count query or a Feign "
        "call — as the root cause WITHOUT reading code. Use for delay faults after "
        "fl_endpoint_anomaly, especially when the jar can't be decompiled."
    )
    input_model = EndpointBreakdownInput

    def is_read_only(self, arguments: EndpointBreakdownInput) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: EndpointBreakdownInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        try:
            ops = get_source().endpoint_breakdown(
                arguments.service, arguments.endpoint, arguments.to_window(), top_n=arguments.top_n
            )
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not ops:
            return ToolResult(
                output=f"No downstream spans found under {arguments.endpoint} in the window "
                "(check the service/endpoint spelling, e.g. from fl_endpoint_anomaly)."
            )
        lines = ["component | operation | calls | errs | total_ms | avg_ms | max_ms"]
        for o in ops:
            lines.append(
                f"{o.component} | {o.operation} | {o.count} | {o.error_count} | "
                f"{o.total_latency_ms:.0f} | {o.avg_latency_ms:.0f} | {o.max_latency_ms:.0f}"
            )
        return ToolResult(output="\n".join(lines))


class ErrorLogsInput(WindowInput):
    service: str | None = Field(default=None, description="Optional service filter")
    pattern: str | None = Field(default=None, description="Optional substring to match in message")
    limit: int = Field(default=30, ge=1, le=200)


class ErrorLogsTool(BaseTool):
    name = "fl_error_logs"
    description = (
        "Fetch ERROR/EXCEPTION log lines in the window, including the logger class "
        "FQN and stack trace when present. The logger FQN is a direct class-level "
        "signal; the stack trace pinpoints the throwing method."
    )
    input_model = ErrorLogsInput

    def is_read_only(self, arguments: ErrorLogsInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ErrorLogsInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            logs = get_source().error_logs(
                arguments.service,
                arguments.to_window(),
                pattern=arguments.pattern,
                limit=arguments.limit,
            )
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if not logs:
            return ToolResult(output="No matching error logs in the given window.")
        lines = []
        for lg in logs:
            head = f"{lg.timestamp} {lg.service} {lg.level}"
            if lg.logger:
                head += f" [{lg.logger}]"
            lines.append(f"{head}\n  {lg.message[:300]}")
        return ToolResult(output="\n".join(lines))
