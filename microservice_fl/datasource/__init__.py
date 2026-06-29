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
    """Return a lazily-constructed default :class:`DuckDBDataSource`.

    Imported lazily so that merely importing the package does not require duckdb
    or an existing database file.
    """
    from microservice_fl.datasource.duckdb_source import DuckDBDataSource

    return DuckDBDataSource()
