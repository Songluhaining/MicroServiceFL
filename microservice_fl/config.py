"""Paths and the deterministic yudao service / module / jar mappings.

These mappings are the backbone of fine-grained localization: observable signals
(metrics, trace endpoints, log lines) carry a *service* name, while the fault
labels and a real fix live at *module / jar / class / method* level. The yudao
naming convention makes every hop deterministic:

    service  yudao-mall-trade
    module   trade                         (the package segment after
                                             cn.iocoder.yudao.module.)
    jar      yudao-module-trade-biz
    class    cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleLogServiceImpl

RPC fan-out is equally self-describing: a Feign call to ``/rpc-api/system/...``
targets the ``system`` module, i.e. the ``yudao-system`` service.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (override via environment variables for other deployments / machines)
# --------------------------------------------------------------------------- #

#: Directory holding the raw collected CSVs (ground_truth/metric/log/trace/...).
DATASET_DIR = Path(
    os.environ.get("OH_FL_DATASET_DIR", r"E:\Myself\赛宝实习\dataset")
)

#: DuckDB database file built by ``python -m microservice_fl.ingest``.
DB_PATH = Path(os.environ.get("OH_FL_DB", str(DATASET_DIR / "fl.duckdb")))

#: Raw CSV file names inside ``DATASET_DIR``.
CSV_FILES = {
    "ground_truth": "ground_truth.csv",
    "phase_timeline": "phase_timeline.csv",
    "metric": "metric.csv",
    "log": "log.csv",
    "trace": "trace.csv",
}

# --------------------------------------------------------------------------- #
# Service <-> module mapping
# --------------------------------------------------------------------------- #
# Canonical list of deployed services and the yudao *module* short-name each one
# corresponds to. Most services are ``yudao-<module>``; the mall services carry a
# ``mall-`` infix in the service name while the module short-name stays bare
# (e.g. service ``yudao-mall-trade`` <-> module ``trade``).

MODULE_BY_SERVICE: dict[str, str] = {
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
}

#: Reverse lookup, module short-name -> service name.
SERVICE_BY_MODULE: dict[str, str] = {m: s for s, m in MODULE_BY_SERVICE.items()}

#: Package prefix every business class shares.
_MODULE_PKG_PREFIX = "cn.iocoder.yudao.module."


def service_to_module(service: str) -> str | None:
    """Return the module short-name for a service, or ``None`` if unknown."""
    return MODULE_BY_SERVICE.get(service)


def module_to_service(module: str) -> str | None:
    """Return the service name for a module short-name, or ``None``."""
    return SERVICE_BY_MODULE.get(module)


def module_to_jar(module: str) -> str:
    """Return the business jar artifact id for a module short-name."""
    return f"yudao-module-{module}-biz"


def service_to_jar(service: str) -> str | None:
    """Return the business jar for a service, or ``None`` if service unknown."""
    module = service_to_module(service)
    return module_to_jar(module) if module else None


def class_fqn_to_module(class_fqn: str) -> str | None:
    """Extract the module short-name from a fully-qualified class name.

    ``cn.iocoder.yudao.module.trade.service...`` -> ``trade``. Returns ``None``
    when the class is not under the standard module package (e.g. framework or
    third-party code), which is itself a useful signal that the fault is not in a
    business module jar.
    """
    if not class_fqn.startswith(_MODULE_PKG_PREFIX):
        return None
    rest = class_fqn[len(_MODULE_PKG_PREFIX):]
    head = rest.split(".", 1)[0]
    return head or None


def class_fqn_to_jar(class_fqn: str) -> str | None:
    """Map a class to its containing business jar, or ``None`` if not a module class."""
    module = class_fqn_to_module(class_fqn)
    return module_to_jar(module) if module else None


def rpc_endpoint_to_service(endpoint: str) -> str | None:
    """Map a Feign RPC endpoint to the callee service.

    ``/rpc-api/system/oauth2/token/check`` -> ``yudao-system``. The second path
    segment is the module short-name. Returns ``None`` when the endpoint is not a
    recognizable ``/rpc-api/<module>/...`` path.
    """
    if not endpoint:
        return None
    parts = [p for p in endpoint.split("/") if p]
    if len(parts) >= 2 and parts[0] == "rpc-api":
        return module_to_service(parts[1])
    return None
