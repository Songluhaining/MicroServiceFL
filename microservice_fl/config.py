"""Paths and the deterministic yudao service / module / jar mappings.

These mappings are the backbone of fine-grained localization: observable signals
(metrics, trace endpoints, log lines) carry a *service* name, while the fault
labels and a real fix live at *module / jar / class / method* level. The yudao
naming convention makes every hop deterministic:

    service  yudao-mall-trade              (spring.application.name in telemetry)
    module   trade                         (the package segment after
                                             cn.iocoder.yudao.module.)
    jar      yudao-module-trade-server     (the deployable jar in yudao-cloud)
    class    cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleLogServiceImpl

RPC fan-out is equally self-describing: a Feign call to ``/rpc-api/system/...``
targets the ``system`` module, i.e. the ``yudao-system`` service.

Naming note: the collected dataset uses ``yudao-<module>`` / ``yudao-mall-<sub>``
service names, while the yudao-cloud source builds jars named
``yudao-module-<module>-server`` (business) and ``-api`` (Feign interfaces).
Telemetry lookups key off the *service* name (left side); jar/decompile lookups
key off the *jar* name (right side); this module bridges the two.
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

#: Grey-box endpoint index (endpoint -> controller class/method/jar), built by
#: ``python -m microservice_fl.greybox.build_index`` from the deployed jars.
INDEX_PATH = Path(os.environ.get("OH_FL_INDEX", str(DATASET_DIR / "endpoint_index.json")))

#: Root holding the built ``*-server`` jars (their ``target/`` dirs), used for
#: on-demand class decompilation. Points at the yudao-cloud checkout by default.
JARS_DIR = Path(os.environ.get("OH_FL_JARS", r"E:\Myself\赛宝实习\yudao-cloud"))

#: CFR decompiler jar, used by ``fl_decompile_class`` to read a single class from
#: its deployed jar (grey-box: source-free). Download once from Maven Central
#: (org.benf:cfr) or https://www.benf.org/other/cfr/.
CFR_JAR = Path(os.environ.get("OH_FL_CFR", str(Path.home() / "tools" / "cfr-0.152.jar")))

# --------------------------------------------------------------------------- #
# Data source selection (offline DuckDB vs live SkyWalking)
# --------------------------------------------------------------------------- #

#: Which DataSource the tools query: ``duckdb`` (offline, ingested) or
#: ``skywalking`` (live OAP for trace + live CSV for metric/log).
DATASOURCE = os.environ.get("OH_FL_DATASOURCE", "duckdb").strip().lower()

#: SkyWalking OAP GraphQL endpoint (live trace/topology/metrics query).
SKYWALKING_URL = os.environ.get("OH_FL_SKYWALKING_URL", "http://127.0.0.1:12800/graphql")

#: Live-appended CSVs the psutil / log collectors write. In ``skywalking`` mode
#: the metric (cpu/mem) and log modalities are read directly from these files,
#: window-filtered, with no ingest (they are always fresh). Empty string = skip
#: that modality.
METRIC_CSV = os.environ.get("OH_FL_METRIC_CSV", str(DATASET_DIR / "metric.csv"))
LOG_CSV = os.environ.get("OH_FL_LOG_CSV", str(DATASET_DIR / "log.csv"))

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
    """Return the deployable business jar artifact id for a module short-name.

    yudao-cloud names it ``yudao-module-<module>-server`` (the Spring Boot app);
    the Feign interface jar is ``yudao-module-<module>-api`` (see
    :func:`module_to_api_jar`).
    """
    return f"yudao-module-{module}-server"


def module_to_api_jar(module: str) -> str:
    """Return the Feign-interface (RPC) jar artifact id for a module short-name."""
    return f"yudao-module-{module}-api"


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
