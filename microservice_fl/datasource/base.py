"""DataSource interface and the result dataclasses shared by the RCA tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    """One injected-fault case from ``ground_truth.csv`` (the eval label).

    ``fault_start`` / ``fault_end`` are ISO-8601 UTC strings as stored in the
    dataset. Everything from ``service`` down is the ground-truth answer and must
    never be shown to the agent at inference time — it is only used to build the
    incident prompt (time window + symptom) and to score predictions.
    """

    case_id: str
    fault_start: str
    fault_end: str
    fault_type: str
    service: str
    module: str
    class_fqn: str
    method: str
    param: str
    trigger_url: str


@dataclass(frozen=True)
class ServiceStat:
    """Per-service metric/error summary over a window, vs a baseline window."""

    service: str
    error_count: int
    span_count: int
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    baseline_avg_latency_ms: float
    baseline_error_rate: float
    cpu_pct: float | None = None
    mem_pct: float | None = None


@dataclass(frozen=True)
class EndpointStat:
    """Per-endpoint latency/error summary within one service over a window."""

    service: str
    endpoint: str
    span_type: str
    count: int
    error_count: int
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    baseline_avg_latency_ms: float
    baseline_p95_latency_ms: float


@dataclass(frozen=True)
class TopologyEdge:
    """A directed caller -> callee edge derived from Feign RPC exit spans."""

    caller: str
    callee: str
    call_count: int
    error_count: int
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True)
class LogEntry:
    """A single log line (typically WARN/EXCEPTION) within a window.

    ``logger`` (the logging class FQN) and ``stack_trace`` are populated when the
    log modality carries them — they are the direct class-level signal. They are
    ``None`` on collections that lack the enriched columns.
    """

    timestamp: str
    service: str
    level: str
    message: str
    logger: str | None = None
    stack_trace: str | None = None


@dataclass(frozen=True)
class ErrorSignal:
    """An aggregated error fingerprint derived from trace error spans.

    Populated from the enriched ``error_type`` / ``error_stack`` trace columns;
    the top stack frame is the bridge from an error span to a class/method.
    """

    service: str
    endpoint: str
    error_type: str
    count: int
    sample_stack: str | None = None


@dataclass(frozen=True)
class OperationStat:
    """A downstream operation (DB / RPC / cache span) under a slow Entry endpoint.

    Produced by :meth:`DataSource.endpoint_breakdown`: within the traces of one
    slow endpoint, spans are grouped by ``(component, operation)`` so the agent
    can name the dominant slow/erroring leaf (a specific SQL, a Feign call, a
    Redis op) as the root cause — **without reading any code**.
    """

    component: str
    operation: str
    count: int
    error_count: int
    avg_latency_ms: float
    total_latency_ms: float
    max_latency_ms: float


@dataclass(frozen=True)
class TimeWindow:
    """An inclusive ISO-8601 UTC time window plus an optional baseline window.

    When ``baseline_start`` / ``baseline_end`` are omitted, implementations pick a
    quiet window of equal length immediately before ``start``.
    """

    start: str
    end: str
    baseline_start: str | None = None
    baseline_end: str | None = None


class DataSource(ABC):
    """Stable query surface over the collected observability data.

    All methods return compact, aggregated results (rankings, percentiles, top-N)
    rather than raw rows — the agent must never receive megabytes of spans/logs.
    """

    # -- ground truth / case replay (eval + interactive replay) ------------- #

    @abstractmethod
    def list_cases(self) -> list[Case]:
        """Return every injected-fault case from ground truth."""

    @abstractmethod
    def get_case(self, case_id: str) -> Case | None:
        """Return one case by id, or ``None``."""

    # -- localization signals ----------------------------------------------- #

    @abstractmethod
    def service_anomalies(
        self, window: TimeWindow, *, top_n: int = 20
    ) -> list[ServiceStat]:
        """Rank services by anomaly (error-rate / latency lift vs baseline)."""

    @abstractmethod
    def endpoint_anomalies(
        self, service: str, window: TimeWindow, *, top_n: int = 20
    ) -> list[EndpointStat]:
        """Rank a service's Entry endpoints by latency/error lift vs baseline."""

    @abstractmethod
    def topology(
        self, window: TimeWindow, *, service: str | None = None
    ) -> list[TopologyEdge]:
        """Return caller->callee RPC edges (optionally filtered to one caller)."""

    @abstractmethod
    def error_logs(
        self,
        service: str | None,
        window: TimeWindow,
        *,
        levels: tuple[str, ...] = ("ERROR", "EXCEPTION"),
        pattern: str | None = None,
        limit: int = 50,
    ) -> list[LogEntry]:
        """Return matching log lines (errors/exceptions) within the window."""

    @abstractmethod
    def span_errors(
        self, service: str | None, window: TimeWindow, *, top_n: int = 20
    ) -> list[ErrorSignal]:
        """Aggregate error spans by (service, endpoint, exception type).

        The top stack frame in ``sample_stack`` is the bridge from an error span
        to the throwing class/method.
        """

    @abstractmethod
    def endpoint_breakdown(
        self, service: str, endpoint: str, window: TimeWindow, *, top_n: int = 15
    ) -> list[OperationStat]:
        """Break a slow Entry endpoint into its downstream operations.

        Within the endpoint's traces, group the non-Entry spans by
        ``(component, operation)`` ranked by total time, so the dominant slow
        leaf (SQL / RPC / cache) is visible as a code-free root cause.
        """

    # -- autonomous monitoring --------------------------------------------- #

    def service_kpis(self, window: TimeWindow) -> dict[str, dict[str, float]]:
        """Per-service numeric KPIs for the statistical detector:
        ``{service: {"cpu", "mem", "latency_ms", "error_count"}}``.

        Default composes :meth:`service_anomalies`; override for a cheaper impl.
        """
        out: dict[str, dict[str, float]] = {}
        for s in self.service_anomalies(window, top_n=100):
            out[s.service] = {
                "cpu": float(s.cpu_pct) if s.cpu_pct is not None else 0.0,
                "mem": float(s.mem_pct) if s.mem_pct is not None else 0.0,
                "latency_ms": float(s.avg_latency_ms),
                "error_count": float(s.error_count),
            }
        return out

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        """Release any underlying resources. Default no-op."""
