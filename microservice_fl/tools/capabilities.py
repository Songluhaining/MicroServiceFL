"""fl_capabilities — probe what fine-grained localization this deployment supports.

The agent should call this FIRST. It detects, at runtime, whether the jars are
parseable (endpoint index present, and a probe class actually decompiles) and
reports the resulting MAXIMUM granularity, so the agent can adapt its plan and
honestly report how deep it can localize:

  * jars parseable  -> method  (index + decompile: full grey-box)
  * index only      -> class   (endpoint->controller from index; root cause from
                                 telemetry, not code — jar encrypted/undecompilable)
  * neither         -> endpoint (telemetry-only; class/method only if a log stack
                                 carries a business frame)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl import config


class CapabilitiesInput(BaseModel):
    pass


class CapabilitiesTool(BaseTool):
    name = "fl_capabilities"
    description = (
        "Probe which localization data sources this deployment has — the endpoint "
        "index, and whether the jars actually decompile — and report the MAXIMUM "
        "reachable granularity (method / class / endpoint). Call this FIRST: if the "
        "jar is encrypted or absent you must localize from telemetry only and lower "
        "the granularity/confidence honestly, rather than assuming class/method."
    )
    input_model = CapabilitiesInput

    def is_read_only(self, arguments: CapabilitiesInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: CapabilitiesInput, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        from microservice_fl.datasource import get_default_source
        from microservice_fl.greybox.decompile import decompile_class
        from microservice_fl.tools.codemap import _load_index

        # -- data modalities: which of trace / metric / log are actually live -- #
        try:
            caps = get_default_source().capabilities()
        except Exception as exc:  # never let the probe crash the tool
            caps = {"trace": False, "metric": False, "log": False,
                    "notes": {"error": str(exc)}}
        trace_ok = bool(caps.get("trace"))
        notes = caps.get("notes", {}) if isinstance(caps.get("notes"), dict) else {}

        idx = _load_index()
        index_ok = bool(idx)

        cfr_present = Path(config.CFR_JAR).exists()
        jars_present = bool(config.JARS_DIR) and Path(config.JARS_DIR).exists()
        decompile_ok = False
        if not cfr_present:
            decompile_note = f"no (CFR jar missing: {config.CFR_JAR})"
        elif not jars_present:
            decompile_note = f"no (jars dir missing: {config.JARS_DIR})"
        elif not index_ok:
            decompile_note = "unprobed (no index to pick a probe class)"
        else:
            sample = next(iter(idx.values()))
            src, note = decompile_class(sample.get("class", ""))
            decompile_ok = bool(src) and ("class " in (src or "") or "public " in (src or ""))
            decompile_note = "yes" if decompile_ok else f"no (jar not decompilable: {note})"

        if index_ok and decompile_ok:
            gran = "method"
            plan = ("jar is parseable — full grey-box: fl_map_endpoint (index) then "
                    "fl_decompile_class the controller and *ServiceImpl to read the "
                    "method body for a line-level root cause.")
        elif index_ok:
            gran = "class"
            plan = ("index maps endpoint->controller but the jar does NOT decompile "
                    "(encrypted/absent): localize the class from the index; get the "
                    "root cause from fl_endpoint_breakdown and error logs, not code.")
        else:
            gran = "endpoint"
            plan = ("no index and jar not parseable — TELEMETRY-ONLY: service/endpoint "
                    "from trace, root cause from fl_endpoint_breakdown; class/method "
                    "only if an exception log (fl_error_logs) carries a business frame.")

        # data modalities override the plan: no trace removes topology + breakdown,
        # so delay faults can only reach service level while exceptions still reach
        # class/method from log stacks. Never claim finer than the data supports.
        if not trace_ok:
            if gran == "method" and caps.get("log"):
                gran = "method (exception via log stack only)"
            else:
                gran = "service"
            plan = ("NO TRACE — fl_topology / fl_endpoint_breakdown / fl_span_errors are "
                    "unavailable, so you CANNOT do root-vs-victim or delay accounting. "
                    "Localize the anomalous SERVICE from metrics (fl_scan_services: cpu/"
                    "mem/error-count) and reach a class/method ONLY for EXCEPTION faults "
                    "from fl_error_logs stacks. Delay faults stay at service level — do "
                    "not invent a downstream cause you cannot see.")

        def mark(ok: bool) -> str:
            return "available" if ok else "ABSENT"

        out = [
            "localization capabilities (probed at runtime):",
            "  -- data modalities --",
            f"  trace  : {mark(trace_ok)}  ({notes.get('trace', '')})",
            f"  metric : {mark(bool(caps.get('metric')))}  ({notes.get('metric', '')})",
            f"  log    : {mark(bool(caps.get('log')))}  ({notes.get('log', '')})",
            "  -- code artifacts --",
            f"  endpoint index : {('available (' + str(len(idx)) + ' endpoints)') if index_ok else 'absent'}",
            f"  jar decompiles : {decompile_note}",
            "",
            f"  => MAX granularity: {gran}",
            f"  plan: {plan}",
        ]
        return ToolResult(output="\n".join(out))
