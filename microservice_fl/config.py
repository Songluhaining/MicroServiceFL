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

#: Hours the *input* time window is ahead of UTC. SkyWalking OAP interprets its
#: Duration in UTC (commonly), so if you type incident windows in local time set
#: this to your offset (e.g. 8 for China/CST) and the SkyWalking queries are
#: converted to UTC for you. 0 = you already type UTC.
try:
    SKYWALKING_TZ_OFFSET = float(os.environ.get("OH_FL_SKYWALKING_TZ_OFFSET", "0"))
except ValueError:
    SKYWALKING_TZ_OFFSET = 0.0

#: Live-appended CSVs the psutil / log collectors write. In ``skywalking`` mode
#: the metric (cpu/mem) and log modalities are read directly from these files,
#: window-filtered, with no ingest (they are always fresh). Empty string = skip
#: that modality. These are also the outputs of ``fl collect`` (see
#: ``microservice_fl.collectors``).
METRIC_CSV = os.environ.get("OH_FL_METRIC_CSV", str(DATASET_DIR / "metric.csv"))
LOG_CSV = os.environ.get("OH_FL_LOG_CSV", str(DATASET_DIR / "log.csv"))

#: Directory where the target system writes its per-service ``<service>.log``
#: files — the source the log collector tails. Deployment-specific.
YUDAO_LOG_DIR = os.environ.get("OH_FL_YUDAO_LOG_DIR", str(DATASET_DIR / "yudao-logs"))

#: How often (seconds) ``fl collect`` samples metrics / polls logs.
try:
    COLLECT_INTERVAL_SEC = int(os.environ.get("OH_FL_COLLECT_INTERVAL", "30"))
except ValueError:
    COLLECT_INTERVAL_SEC = 30

#: How many hours of metric/log CSV to keep — older rows are pruned by
#: ``fl collect`` so the live CSVs don't grow without bound (trace retention is
#: SkyWalking's own recordDataTTL). 0 disables pruning.
try:
    RETENTION_HOURS = int(os.environ.get("OH_FL_RETENTION_HOURS", "24"))
except ValueError:
    RETENTION_HOURS = 24

#: Raw CSV file names inside ``DATASET_DIR``.
CSV_FILES = {
    "ground_truth": "ground_truth.csv",
    "phase_timeline": "phase_timeline.csv",
    "metric": "metric.csv",
    "log": "log.csv",
    "trace": "trace.csv",
}

# --------------------------------------------------------------------------- #
# Service <-> module <-> jar <-> class mapping (delegated to the target profile)
# --------------------------------------------------------------------------- #
# The naming conventions are not hardcoded here — they come from the active
# :class:`~microservice_fl.target.TargetProfile`, selected by ``OH_FL_TARGET``
# (default: the built-in ``yudao-cloud`` profile). This keeps the public function
# names below stable while letting a different microservice system be onboarded
# with a JSON profile instead of code changes.

import functools


@functools.lru_cache(maxsize=1)
def active_target():  # type: ignore[no-untyped-def]
    """Return the active TargetProfile (cached; honours ``OH_FL_TARGET``)."""
    from microservice_fl.target import load_target

    return load_target()


def service_to_module(service: str) -> str | None:
    """Return the module short-name for a service, or ``None`` if unknown."""
    return active_target().service_to_module(service)


def module_to_service(module: str) -> str | None:
    """Return the service name for a module short-name, or ``None``."""
    return active_target().module_to_service(module)


def module_to_jar(module: str) -> str:
    """Return the deployable business jar artifact id for a module short-name."""
    return active_target().module_to_jar(module)


def module_to_api_jar(module: str) -> str:
    """Return the Feign-interface (RPC) jar artifact id for a module short-name."""
    return active_target().module_to_api_jar(module)


def service_to_jar(service: str) -> str | None:
    """Return the business jar for a service, or ``None`` if service unknown."""
    module = service_to_module(service)
    return module_to_jar(module) if module else None


def class_fqn_to_module(class_fqn: str) -> str | None:
    """Extract the module short-name from a fully-qualified class name.

    Returns ``None`` when the class is not under the profile's module package
    (e.g. framework/third-party code) — itself a signal that the fault is not in a
    business module jar.
    """
    return active_target().class_fqn_to_module(class_fqn)


def class_fqn_to_jar(class_fqn: str) -> str | None:
    """Map a class to its containing business jar, or ``None`` if not a module class."""
    module = class_fqn_to_module(class_fqn)
    return module_to_jar(module) if module else None


def rpc_endpoint_to_service(endpoint: str) -> str | None:
    """Map a Feign RPC endpoint (``/rpc-api/<module>/...``) to the callee service."""
    return active_target().rpc_endpoint_to_service(endpoint)
