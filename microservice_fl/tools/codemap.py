"""Grey-box code-mapping tools: endpoint/class → module / jar / service / class.

``fl_map_endpoint`` resolves a trace endpoint to the exact controller class and
method by querying the **grey-box endpoint index** (built offline from the
deployed jars — see ``microservice_fl.greybox.build_index``). No source tree is
read at diagnosis time; when the index is missing it degrades to the
deterministic module/jar mapping plus a grep hint.
"""

from __future__ import annotations

import functools
import json

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl import config

_VERBS = ("GET", "POST", "PUT", "DELETE", "PATCH", "ANY")


def _split_endpoint(endpoint: str) -> tuple[str, list[str]]:
    """Split ``GET:/admin-api/crm/business/page`` -> ('GET', ['admin-api','crm',...])."""
    method = ""
    path = endpoint
    if ":" in endpoint and endpoint.split(":", 1)[0].isupper():
        method, path = endpoint.split(":", 1)
    parts = [p for p in path.split("/") if p]
    return method, parts


@functools.lru_cache(maxsize=1)
def _load_index() -> dict:
    """Load the grey-box endpoint index (endpoint -> {class, method, jar})."""
    p = config.INDEX_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def reset_index_cache() -> None:
    """Drop the cached index (tests / after a rebuild)."""
    _load_index.cache_clear()


def _lookup_endpoint(method: str, parts: list[str]) -> tuple[str | None, dict | None]:
    """Find the controller entry for an endpoint; try the given verb then any verb."""
    idx = _load_index()
    if not idx:
        return None, None
    path = "/" + "/".join(parts)
    if method:
        hit = idx.get(f"{method}:{path}")
        if hit:
            return f"{method}:{path}", hit
    for verb in _VERBS:
        hit = idx.get(f"{verb}:{path}")
        if hit:
            return f"{verb}:{path}", hit
    return None, None


class MapEndpointInput(BaseModel):
    endpoint: str = Field(
        description="Trace endpoint or URL, e.g. 'GET:/admin-api/crm/business/page' "
        "or '/admin-api/crm/business/page'"
    )


class MapEndpointTool(BaseTool):
    name = "fl_map_endpoint"
    description = (
        "Map a trace endpoint / URL to its yudao module, jar, owning service, and "
        "the exact controller class + method — resolved from the grey-box endpoint "
        "index built from the deployed jars (no source needed). Use after "
        "fl_endpoint_anomaly to go from a slow endpoint to a class/method; then "
        "fl_decompile_class the controller to follow its call into the *ServiceImpl."
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

        out = [
            f"endpoint: {arguments.endpoint}",
            f"  http_method : {method or '(unknown)'}",
            f"  api_kind    : {api_kind}",
            f"  module      : {module}",
            f"  service     : {service or '(unknown service for module)'}",
            f"  jar         : {jar}",
        ]

        key, hit = _lookup_endpoint(method, parts)
        if hit:
            out += [
                "",
                "resolved from grey-box index (deployed jar, no source):",
                f"  matched     : {key}",
                f"  controller  : {hit['class']}",
                f"  method      : {hit['method']}",
                f"  in jar      : {hit.get('jar', jar)}",
                "",
                "next: fl_decompile_class on the controller to see which "
                f"cn.iocoder.yudao.module.{module}.service.*Service it calls "
                f"(conventionally the same method name '{hit['method']}'), then "
                "fl_decompile_class that *ServiceImpl to read the method body.",
            ]
            return ToolResult(output="\n".join(out))

        # Fallback: index unavailable or endpoint not indexed.
        last = parts[-1] if len(parts) > 2 else module
        idx_note = ("(endpoint index empty — run "
                    "`python -m microservice_fl.greybox.build_index`)"
                    if not _load_index() else "(endpoint not found in index)")
        out += [
            f"  path_tail   : /{tail}",
            "",
            f"controller class/method not resolved {idx_note}. Fallback — grep the source:",
            f"    grep -rn '\"{last}\"' --include=*.java   (module jar: {jar})",
            f"  then follow the controller method into "
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
        "'yudao-module-trade-server'). Returns no module when the class is framework/"
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
