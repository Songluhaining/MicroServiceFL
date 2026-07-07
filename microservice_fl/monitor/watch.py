"""Autonomous monitor loop: sample metric KPIs, detect statistically, auto-locate.

Each tick samples per-service KPIs (cpu / mem / latency / error-count), feeds each
series to the statistical detector, and — when a series breaches its rolling
distribution threshold — auto-runs the localization agent (`/locate`) for the
anomalous service, writing the report to the incidents dir. A cooldown prevents
re-localizing the same ongoing incident.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from microservice_fl.datasource import get_default_source
from microservice_fl.datasource.base import TimeWindow
from microservice_fl.monitor.detector import StatDetector

_METRICS = ("cpu", "mem", "latency_ms", "error_count")
_FAULT_HINT = {"latency_ms": "delay", "error_count": "exception",
               "cpu": "resource", "mem": "resource"}
#: floor on each series' spread so a jump from a flat baseline still fires
#: (cpu/mem in %, latency in ms, error_count in rows/window)
_MIN_SCALE = {"cpu": 2.0, "mem": 1.0, "latency_ms": 20.0, "error_count": 1.0}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _symptom(a: dict) -> str:
    svc, m, v = a["service"], a["metric"], a["value"]
    if m == "latency_ms":
        return (f"{svc} latency p95 spiked to {v:.0f}ms "
                f"(> statistical threshold {a['threshold']:.0f}, {a['score']:.1f}σ)")
    if m == "error_count":
        return f"{svc} error-log count spiked to {v:.0f} in the window (> statistical threshold)"
    return f"{svc} {m} spiked to {v:.1f}% (> statistical threshold {a['threshold']:.1f})"


def _localize(anom: dict, now: datetime, locate_window_sec: int, out: Path, log) -> None:
    start = _fmt(now - timedelta(seconds=locate_window_sec))
    end = _fmt(now)
    prompt = f"/locate time={start}~{end} symptom={_symptom(anom)}"
    log(f"[watch] localizing -> {prompt}")
    ts = now.strftime("%Y%m%d-%H%M%S")
    report = out / f"incident-{ts}-{anom['service']}.txt"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "openharness", "-p", prompt,
             "--output-format", "text", "--permission-mode", "auto"],
            capture_output=True, text=True, timeout=900,
        )
        body = proc.stdout or proc.stderr
    except Exception as exc:  # never let localization crash the monitor
        body = f"localization failed: {exc}"
    report.write_text(f"# incident {ts}\n# trigger: {anom}\n# {prompt}\n\n{body}\n",
                      encoding="utf-8")
    log(f"[watch] report -> {report}")


def watch(*, interval: int = 60, window_sec: int = 180, locate_window_sec: int = 300,
          cooldown: int = 600, out_dir: str | None = None, k: float = 3.0,
          warmup: int = 15, once: bool = False, log=print) -> None:
    src = get_default_source()
    det = StatDetector(k=k, warmup=warmup)
    out = Path(out_dir) if out_dir else (Path.cwd() / "incidents")
    out.mkdir(parents=True, exist_ok=True)
    last_fire = 0.0
    log(f"[watch] source={type(src).__name__} interval={interval}s window={window_sec}s "
        f"k={k} warmup={warmup} out={out}")

    while True:
        now = datetime.now()
        w = TimeWindow(start=_fmt(now - timedelta(seconds=window_sec)), end=_fmt(now))
        try:
            kpis = src.service_kpis(w)
        except Exception as exc:
            log(f"[watch] KPI sample error: {exc}")
            kpis = {}

        anomalies = []
        for svc, vals in kpis.items():
            for m in _METRICS:
                hit = det.update(f"{m}:{svc}", float(vals.get(m, 0.0)),
                                 min_scale=_MIN_SCALE[m])
                if hit:
                    anomalies.append({**hit, "service": svc, "metric": m,
                                      "fault_hint": _FAULT_HINT[m]})

        if anomalies:
            top = max(anomalies, key=lambda a: a["score"])
            log(f"[watch] ANOMALY {top['metric']}:{top['service']} "
                f"value={top['value']} score={top['score']}σ (of {len(anomalies)} series)")
            if time.time() - last_fire >= cooldown:
                last_fire = time.time()
                _localize(top, now, locate_window_sec, out, log)
            else:
                log("[watch] within cooldown — not re-localizing")

        if once:
            break
        time.sleep(interval)
