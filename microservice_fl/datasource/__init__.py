"""Data access layer for MicroServiceFL.

``DataSource`` is the stable interface the RCA tools depend on. The offline
implementation (``DuckDBDataSource``) queries the pre-ingested DuckDB database;
a future real-time implementation can query SkyWalking / Prometheus / Loki
without changing any tool or agent code.
"""

from __future__ import annotations

from microservice_fl.datasource.base import (
    Case,
    DataSource,
    EndpointStat,
    LogEntry,
    ServiceStat,
    TopologyEdge,
)

__all__ = [
    "Case",
    "DataSource",
    "EndpointStat",
    "LogEntry",
    "ServiceStat",
    "TopologyEdge",
    "get_default_source",
]


def get_default_source():  # type: ignore[no-untyped-def]
    """Return the configured default DataSource (lazy import).

    ``OH_FL_DATASOURCE`` selects the backend: ``duckdb`` (offline, ingested;
    default) or ``skywalking`` (live OAP for trace + live CSV for metric/log).
    Imported lazily so the package imports without duckdb/httpx or a database.
    """
    from microservice_fl import config

    if config.DATASOURCE == "skywalking":
        from microservice_fl.datasource.skywalking_source import SkyWalkingDataSource

        return SkyWalkingDataSource()

    from microservice_fl.datasource.duckdb_source import DuckDBDataSource

    return DuckDBDataSource()
