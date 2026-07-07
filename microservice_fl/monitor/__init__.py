"""Autonomous monitoring: statistical anomaly detection on metric series that
auto-triggers the localization agent.

Detection is deterministic and cheap — a rolling statistical baseline per metric
series (cpu / mem / latency / error-count), flagging values beyond a
distribution threshold. Trace and log *content* are NOT reduced to thresholds
here; they are analysed by the agent once a metric anomaly triggers localization.
"""

from microservice_fl.monitor.detector import StatDetector

__all__ = ["StatDetector"]
