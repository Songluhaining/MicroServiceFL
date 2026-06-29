"""Deterministic code-mapping tools: endpoint/class → module / jar / service."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl import config


def _split_endpoint(endpoint: str) -> tuple[str, list[str]]:
    """Split ``GET:/admin-api/crm/business/page`` -> ('GET', ['admin-api','crm',...])."""
    method = ""
    path = endpoint
    if ":" in endpoint and endpoint.split(":", 1)[0].isupper():
        method, path = endpoint.split(":", 1)
    parts = [p for p in path.split("/") if p]
    return method, parts


class MapEndpointInput(BaseModel):
    endpoint: str = Field(
        description="Trace endpoint or URL, e.g. 'GET:/admin-api/crm/business/page' "
        "or '/admin-api/crm/business/page'"
    )


class MapEndpointTool(BaseTool):
    name = "fl_map_endpoint"
    description = (
        "Map a trace endpoint / URL to its yudao module, jar and owning service "
        "(deterministic), and return a grep recipe to find the exact "
        "controller→service class/method in the yudao source tree. Use after "
        "fl_endpoint_anomaly to go from a slow endpoint to a class/method."
    )
    input_model = MapEndpointInput

    def is_read_only(self, arguments: MapEndpointInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: MapEndpointInput, context: ToolExecutionContext) -> ToolResult:
        del context
        method, parts = _split_endpoint(arguments.endpoint)
        if len(parts) < 2 or parts[0] not in {"admin-api", "rpc-api", "app-api"}:
            return ToolResult(
                output=f"Unrecognized endpoint shape: {arguments.endpoint!r}. "
                "Expected '/admin-api/<module>/...' or '/rpc-api/<module>/...'.",
                is_error=True,
            )
        api_kind, module = parts[0], parts[1]
        service = config.module_to_service(module)
        jar = config.module_to_jar(module)
        tail = "/".join(parts[2:])
        # The controller @RequestMapping for a yudao module is conventionally
        # "/<api-prefix>/<module>" and method-level @GetMapping/@PostMapping carry
        # the remaining path segment(s).
        last = parts[-1] if len(parts) > 2 else module
        out = [
            f"endpoint: {arguments.endpoint}",
            f"  http_method : {method or '(unknown)'}",
            f"  api_kind    : {api_kind}",
            f"  module      : {module}",
            f"  service     : {service or '(unknown service for module)'}",
            f"  jar         : {jar}",
            f"  path_tail   : /{tail}",
            "",
            "To resolve the exact class & method, search the yudao source:",
            f"  grep for the route segment in the module's controller package:",
            f"    grep -rn '\"{last}\"' --include=*.java  (modules ending in -{module}-)",
            f"  then follow the controller method's service call into "
            f"cn.iocoder.yudao.module.{module}.service.*Impl",
        ]
        return ToolResult(output="\n".join(out))


class ClassToJarInput(BaseModel):
    class_fqn: str = Field(description="Fully-qualified Java class name")


class ClassToJarTool(BaseTool):
    name = "fl_class_to_jar"
    description = (
        "Map a fully-qualified class name to its yudao module and business jar "
        "(e.g. cn.iocoder.yudao.module.trade...Impl → module 'trade', jar "
        "'yudao-module-trade-biz'). Returns no module when the class is framework/"
        "third-party code, which itself indicates the fault is outside a business jar."
    )
    input_model = ClassToJarInput

    def is_read_only(self, arguments: ClassToJarInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ClassToJarInput, context: ToolExecutionContext) -> ToolResult:
        del context
        module = config.class_fqn_to_module(arguments.class_fqn)
        if not module:
            return ToolResult(
                output=f"{arguments.class_fqn}\n  module: (not a cn.iocoder.yudao.module.* "
                "business class — likely framework/starter/third-party code)"
            )
        return ToolResult(
            output=(
                f"{arguments.class_fqn}\n"
                f"  module : {module}\n"
                f"  jar    : {config.module_to_jar(module)}\n"
                f"  service: {config.module_to_service(module) or '(unknown)'}"
            )
        )
