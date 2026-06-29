"""MicroServiceFL — fault-localization layer for the yudao microservice system.

This package is intentionally decoupled from the OpenHarness harness core: it
owns the dataset access (DuckDB over the collected metric/trace/log CSVs), the
deterministic yudao service/module/jar mappings, the RCA tools exposed to the
agent, and the offline evaluation harness. The only touch-point inside
``openharness`` is a small registration edit in ``openharness.tools`` that wires
the ``fl_*`` tools into the default tool registry.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
