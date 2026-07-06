"""Real-time metric (psutil) and log collectors.

An optional add-on feature of the localizer: these run as long-lived processes
that write the metric/log CSVs the live SkyWalkingDataSource reads (cpu/mem and
error logs), so all three modalities are available in production without any
ingest. Trace stays live via SkyWalking OAP. Run with ``fl collect``.

Timestamps are written in **UTC** (matching the trace store); the DataSource
applies OH_FL_SKYWALKING_TZ_OFFSET when reading, so a local-time incident window
lines up across all three modalities.
"""

from microservice_fl.collectors.core import discover_pids, run_logs, run_metrics

__all__ = ["discover_pids", "run_metrics", "run_logs"]
