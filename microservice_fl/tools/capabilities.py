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
        from microservice_fl.greybox.decompile import decompile_class
        from microservice_fl.tools.codemap import _load_index

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

        out = [
            "localization capabilities (probed at runtime):",
            f"  endpoint index : {('available (' + str(len(idx)) + ' endpoints)') if index_ok else 'absent'}",
            f"  jar decompiles : {decompile_note}",
            "",
            f"  => MAX granularity: {gran}",
            f"  plan: {plan}",
        ]
        return ToolResult(output="\n".join(out))
