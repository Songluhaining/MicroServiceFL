"""Statistical anomaly detection over numeric metric series.

Per series (e.g. ``cpu:yudao-system``), keep a rolling window of recent *normal*
samples as the baseline distribution and flag a value that exceeds a
distribution threshold — robust median + k·MAD (falling back to mean + k·σ),
i.e. the classic statistical / 3-sigma rule. A breach must persist for
``consecutive`` samples to fire, and anomalous samples do **not** pollute the
baseline (so a sustained fault doesn't quietly become the new normal).

All four monitored series are "high is bad" (cpu, mem, latency, error-count), so
only the upper bound is checked.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque

#: scale factor making MAD a consistent estimator of the standard deviation
_MAD_TO_STD = 1.4826


class StatDetector:
    def __init__(
        self, *, history: int = 120, k: float = 3.0, warmup: int = 15, consecutive: int = 2
    ) -> None:
        #: max baseline samples kept per series
        self.history = history
        #: threshold = center + k * scale
        self.k = k
        #: samples needed before detection starts (learn normal first)
        self.warmup = warmup
        #: consecutive breaches required to fire (debounce single spikes)
        self.consecutive = consecutive
        self._hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history))
        self._breach: dict[str, int] = defaultdict(int)

    def update(self, key: str, value: float) -> dict | None:
        """Feed one sample for series ``key``; return an anomaly dict or ``None``.

        Fires when ``value`` exceeds the rolling distribution threshold for
        ``consecutive`` samples. In-range values extend the baseline; breaching
        values are held out of it.
        """
        h = self._hist[key]
        if len(h) < self.warmup:
            h.append(value)  # warm-up: accept everything as normal
            return None

        center, scale = self._center_scale(h)
        threshold = center + self.k * scale
        if scale > 0 and value > threshold:
            self._breach[key] += 1
            if self._breach[key] >= self.consecutive:
                return {
                    "key": key,
                    "value": round(value, 3),
                    "threshold": round(threshold, 3),
                    "score": round((value - center) / scale, 2),
                    "baseline_n": len(h),
                }
            return None
        # normal sample: reset the breach counter and learn from it
        self._breach[key] = 0
        h.append(value)
        return None

    @staticmethod
    def _center_scale(h: deque[float]) -> tuple[float, float]:
        vals = list(h)
        med = statistics.median(vals)
        mad = statistics.median([abs(x - med) for x in vals])
        if mad > 0:
            return med, _MAD_TO_STD * mad
        # degenerate MAD (many identical values) -> fall back to mean/std
        return statistics.fmean(vals), statistics.pstdev(vals)
