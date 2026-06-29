"""Case-browsing tools for interactive replay of recorded incidents.

These expose only the *incident description* a real operator would report — the
time window, the fault kind, and the failing request URL. They deliberately
withhold the ground-truth localization (service / module / class / method) so
that calling them during a localization run cannot leak the answer. The offline
evaluation harness reads the full ground truth via the DataSource directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl.tools._shared import get_source
from microservice_fl.tools.signals import _missing_db_result


def _symptom(fault_type: str, trigger_url: str) -> str:
    if fault_type == "delay":
        return f"requests to {trigger_url} are slow / timing out"
    if fault_type == "exception":
        return f"requests to {trigger_url} are failing with errors"
    return f"{fault_type} affecting {trigger_url}"


class ListCasesTool(BaseTool):
    name = "fl_list_cases"
    description = (
        "List recorded incident cases (id, time window, fault kind, failing URL) "
        "for interactive replay. Does NOT reveal the localization answer."
    )
    input_model = type("ListCasesInput", (BaseModel,), {})  # no arguments

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        try:
            cases = get_source().list_cases()
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        lines = ["case_id | window | kind | failing_url"]
        for c in cases:
            lines.append(
                f"{c.case_id} | {c.fault_start}..{c.fault_end} | {c.fault_type} | {c.trigger_url}"
            )
        return ToolResult(output="\n".join(lines))


class GetCaseInput(BaseModel):
    case_id: str = Field(description="Case id, e.g. case00005")


class GetCaseTool(BaseTool):
    name = "fl_get_case"
    description = (
        "Return one recorded incident as an operator-style report: time window + "
        "symptom + failing URL. Does NOT reveal service/class/method (the answer)."
    )
    input_model = GetCaseInput

    def is_read_only(self, arguments: GetCaseInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GetCaseInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            case = get_source().get_case(arguments.case_id)
        except FileNotFoundError as exc:
            return _missing_db_result(exc)
        if case is None:
            return ToolResult(output=f"No such case: {arguments.case_id}", is_error=True)
        return ToolResult(
            output=(
                f"case_id : {case.case_id}\n"
                f"window  : {case.fault_start} .. {case.fault_end}\n"
                f"kind    : {case.fault_type}\n"
                f"symptom : {_symptom(case.fault_type, case.trigger_url)}"
            )
        )
