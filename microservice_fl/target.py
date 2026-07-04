"""Target-system profiles: the naming conventions that make localization work.

Everything that ties the localizer to *one* microservice system — how a telemetry
service name maps to a module, jar and Java package, and the API path prefixes —
lives in a :class:`TargetProfile`, not in code. yudao-cloud ships as the default
profile; onboarding a different Spring Cloud system (or a yudao fork with other
names) is a JSON file, no code change.

Selection (via ``OH_FL_TARGET``):
  * unset            -> the built-in ``yudao-cloud`` profile
  * a built-in name  -> that built-in profile
  * a path to a JSON -> loaded from disk
  * a bare name      -> ``~/.openharness/fl_targets/<name>.json``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TargetProfile:
    """Naming/convention rules for one target microservice system."""

    name: str
    #: Shared Java package prefix, e.g. ``cn.iocoder.yudao.module.`` — the segment
    #: right after it is the module short-name.
    package_prefix: str
    #: Business jar name template, ``{module}`` filled in (yudao: ``-server``).
    jar_template: str
    #: RPC / Feign-interface jar name template (yudao: ``-api``).
    api_jar_template: str
    #: Telemetry service name -> module short-name (the authoritative mapping).
    module_by_service: dict[str, str] = field(default_factory=dict)
    #: HTTP entry / RPC path prefixes used to parse endpoints.
    admin_api_prefix: str = "admin-api"
    rpc_api_prefix: str = "rpc-api"

    # -- derived lookups ---------------------------------------------------- #

    def service_to_module(self, service: str) -> str | None:
        return self.module_by_service.get(service)

    def module_to_service(self, module: str) -> str | None:
        for svc, mod in self.module_by_service.items():
            if mod == module:
                return svc
        return None

    def module_to_jar(self, module: str) -> str:
        return self.jar_template.format(module=module)

    def module_to_api_jar(self, module: str) -> str:
        return self.api_jar_template.format(module=module)

    def class_fqn_to_module(self, class_fqn: str) -> str | None:
        if not class_fqn.startswith(self.package_prefix):
            return None
        rest = class_fqn[len(self.package_prefix):]
        head = rest.split(".", 1)[0]
        return head or None

    def rpc_endpoint_to_service(self, endpoint: str) -> str | None:
        if not endpoint:
            return None
        parts = [p for p in endpoint.split("/") if p]
        if len(parts) >= 2 and parts[0] == self.rpc_api_prefix:
            return self.module_to_service(parts[1])
        return None

    # -- (de)serialization -------------------------------------------------- #

    @classmethod
    def from_dict(cls, data: dict) -> TargetProfile:
        return cls(
            name=data["name"],
            package_prefix=data["package_prefix"],
            jar_template=data["jar_template"],
            api_jar_template=data.get("api_jar_template", data["jar_template"]),
            module_by_service=dict(data.get("module_by_service", {})),
            admin_api_prefix=data.get("admin_api_prefix", "admin-api"),
            rpc_api_prefix=data.get("rpc_api_prefix", "rpc-api"),
        )


#: The default target: yudao-cloud (telemetry uses yudao-<module> / yudao-mall-<sub>
#: names; jars are yudao-module-<module>-server / -api).
_YUDAO_CLOUD = TargetProfile(
    name="yudao-cloud",
    package_prefix="cn.iocoder.yudao.module.",
    jar_template="yudao-module-{module}-server",
    api_jar_template="yudao-module-{module}-api",
    module_by_service={
        "yudao-system": "system",
        "yudao-infra": "infra",
        "yudao-bpm": "bpm",
        "yudao-crm": "crm",
        "yudao-erp": "erp",
        "yudao-member": "member",
        "yudao-mall-product": "product",
        "yudao-mall-promotion": "promotion",
        "yudao-mall-statistics": "statistics",
        "yudao-mall-trade": "trade",
    },
)

_BUILTINS: dict[str, TargetProfile] = {p.name: p for p in (_YUDAO_CLOUD,)}


def _user_targets_dir() -> Path:
    return Path.home() / ".openharness" / "fl_targets"


def load_target(spec: str | None = None) -> TargetProfile:
    """Resolve a :class:`TargetProfile` from ``spec`` / ``OH_FL_TARGET``."""
    spec = (spec if spec is not None else os.environ.get("OH_FL_TARGET", "")).strip()
    if not spec:
        return _YUDAO_CLOUD
    # explicit path to a JSON file
    p = Path(spec)
    if p.suffix.lower() == ".json" and p.exists():
        return TargetProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))
    # built-in name
    if spec in _BUILTINS:
        return _BUILTINS[spec]
    # user profile by name
    cand = _user_targets_dir() / f"{spec}.json"
    if cand.exists():
        return TargetProfile.from_dict(json.loads(cand.read_text(encoding="utf-8")))
    raise ValueError(
        f"unknown target profile {spec!r}. Built-ins: {sorted(_BUILTINS)}; "
        f"or drop a JSON at {cand}, or set OH_FL_TARGET to a .json path."
    )


def available_targets() -> list[str]:
    """Names of built-in + user-dir profiles (for the CLI)."""
    names = set(_BUILTINS)
    d = _user_targets_dir()
    if d.exists():
        names.update(p.stem for p in d.glob("*.json"))
    return sorted(names)
