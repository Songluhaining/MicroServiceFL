"""Shared plumbing for the fault-localization tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from microservice_fl.datasource.base import DataSource, TimeWindow

_SOURCE: DataSource | None = None


def get_source() -> DataSource:
    """Return a cached default DataSource, constructing it on first use."""
    global _SOURCE
    if _SOURCE is None:
        from microservice_fl.datasource import get_default_source

        _SOURCE = get_default_source()
    return _SOURCE


def reset_source() -> None:
    """Drop the cached DataSource (tests / db rebuilds)."""
    global _SOURCE
    if _SOURCE is not None:
        _SOURCE.close()
    _SOURCE = None


class WindowInput(BaseModel):
    """A fault time window, plus an optional explicit baseline window.

    Times are ISO-8601 UTC, e.g. ``2026-06-05T01:03:31Z``. When the baseline is
    omitted, the equal-length quiet period immediately before ``start`` is used.
    """

    start: str = Field(description="Fault window start, ISO-8601 UTC (…Z)")
    end: str = Field(description="Fault window end, ISO-8601 UTC (…Z)")
    baseline_start: str | None = Field(default=None, description="Optional baseline start")
    baseline_end: str | None = Field(default=None, description="Optional baseline end")

    def to_window(self) -> TimeWindow:
        return TimeWindow(
            start=self.start,
            end=self.end,
            baseline_start=self.baseline_start,
            baseline_end=self.baseline_end,
        )


def lift(current: float, baseline: float) -> str:
    """Format a current-vs-baseline lift compactly, e.g. ``120ms→2100ms (x17)``."""
    if baseline <= 0:
        return f"{current:.0f} (baseline ~0)"
    ratio = current / baseline
    return f"{baseline:.0f}→{current:.0f} (x{ratio:.1f})"
