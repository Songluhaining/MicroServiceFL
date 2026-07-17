"""Metric (psutil) + log collectors that feed the live CSVs.

Adapted from the dataset-collection script's MetricCollector/LogCollector, but
standalone and label-free (production has no ground-truth labels). Service→PID
discovery reuses the active target profile's jar names, so it works for any
configured target, not just yudao hardcoding.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from microservice_fl import config

#: one lock per CSV path so appends and retention-pruning don't interleave
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

_METRIC_HEADER = [
    "timestamp", "service", "level", "cpu_pct", "mem_pct", "mem_used", "mem_avail",
    "swap_pct", "disk_pct", "disk_free", "net_sent", "net_recv",
    "proc_cpu_pct", "proc_mem_rss", "proc_mem_pct", "label", "fault_root", "fault_type",
]
_LOG_HEADER = ["timestamp", "service", "level", "message", "label", "fault_root"]
#: terminal colour escape sequences logback console layouts leak into log files
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: continuation lines of a Java stack trace (kept with their error header so the
#: business frame — the class/method that threw — survives into the log CSV).
_STACK = re.compile(r"^\s*(at\s|Caused by:|\.\.\.\s*\d+\s*more|Suppressed:)")


def _classify(line: str) -> str | None:
    """Real level of a log line, or ``None`` if it should not be recorded as error.

    Keyed off the explicit level token, NOT the mere presence of the word
    "Exception" — an INFO/DEBUG record that merely mentions an exception is not an
    error (the old ``(ERROR|Exception|WARN)`` match mislabelled those as EXCEPTION
    and inflated the error count). A bare stack header with no level token but an
    exception class is EXCEPTION.
    """
    if re.search(r"\bERROR\b", line):
        return "ERROR"
    if re.search(r"\bWARN\b", line):
        return "WARN"
    if re.search(r"\b(?:INFO|DEBUG|TRACE)\b", line):
        return None
    if re.search(r"\b[\w.]+(?:Exception|Error)\b", line):
        return "EXCEPTION"
    return None


def _ts() -> str:
    """UTC timestamp in the canonical ISO form the DataSource parses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _init_csv(path: str, header: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.stat().st_size == 0:
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)


def _append(path: str, row: list) -> None:
    with _LOCKS[path]:
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
            f.flush()


def prune_csv(path: str, retention_hours: int) -> int:
    """Drop rows older than ``retention_hours`` (by the UTC timestamp column).

    Returns the number of rows kept. ISO ``…Z`` timestamps sort lexicographically,
    so a string compare against the cutoff is exact.
    """
    p = Path(path)
    if retention_hours <= 0 or not p.exists():
        return -1
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with _LOCKS[path]:
        with open(p, encoding="utf-8", newline="") as f:
            rows = f.readlines()
        if len(rows) <= 1:
            return max(0, len(rows) - 1)
        kept = [r for r in rows[1:] if r.split(",", 1)[0] >= cutoff]
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(rows[0])
            f.writelines(kept)
        tmp.replace(p)
    return len(kept)


def run_retention(retention_hours: int | None = None, interval: int = 3600,
                  stop: threading.Event | None = None) -> None:
    """Periodically prune the metric/log CSVs to the retention window."""
    retention_hours = retention_hours if retention_hours is not None else config.RETENTION_HOURS
    stop = stop or threading.Event()
    if retention_hours <= 0:
        return
    while not stop.is_set():
        stop.wait(interval)
        for path in (config.METRIC_CSV, config.LOG_CSV):
            try:
                prune_csv(path, retention_hours)
            except Exception:
                pass


def discover_pids() -> dict[str, int]:
    """Map service name -> JVM pid via ``jps -l``, matching profile jar names."""
    prof = config.active_target()
    keyword: dict[str, str] = {
        svc: config.module_to_jar(mod) for svc, mod in prof.module_by_service.items()
    }
    keyword.setdefault("yudao-gateway", "yudao-gateway")
    try:
        out = subprocess.run(["jps", "-l"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return {}
    pid_map: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid, name = parts[0], parts[1]
        for svc, kw in keyword.items():
            if kw in name:
                pid_map[svc] = int(pid)
    return pid_map


def run_metrics(interval: int | None = None, stop: threading.Event | None = None) -> None:
    """Sample system + per-service (cpu/mem) metrics into METRIC_CSV until stopped."""
    import psutil

    interval = interval or config.COLLECT_INTERVAL_SEC
    stop = stop or threading.Event()
    _init_csv(config.METRIC_CSV, _METRIC_HEADER)

    procs: dict[str, "psutil.Process"] = {}
    for svc, pid in discover_pids().items():
        try:
            procs[svc] = psutil.Process(pid)
        except Exception:
            pass
    psutil.cpu_percent(interval=None)  # prime the counter

    while not stop.is_set():
        t = _ts()
        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        _append(config.METRIC_CSV, [
            t, "_system_", "host", cpu, vm.percent, vm.used, vm.available,
            sw.percent, disk.percent, disk.free, net.bytes_sent, net.bytes_recv,
            "", "", "", "normal", "", "",
        ])
        for svc, p in procs.items():
            try:
                with p.oneshot():
                    pcpu = p.cpu_percent(interval=None)
                    pmem = p.memory_info().rss
                    pmem_pct = p.memory_percent()
                _append(config.METRIC_CSV, [
                    t, svc, "process", "", "", "", "", "", "", "", "", "",
                    pcpu, pmem, pmem_pct, "normal", "", "",
                ])
            except Exception:
                pass
        stop.wait(interval)


def run_logs(interval: int | None = None, stop: threading.Event | None = None) -> None:
    """Tail per-service ``<service>.log`` for ERROR/WARN/Exception into LOG_CSV."""
    interval = interval or config.COLLECT_INTERVAL_SEC
    stop = stop or threading.Event()
    _init_csv(config.LOG_CSV, _LOG_HEADER)

    log_dir = config.YUDAO_LOG_DIR
    files: dict[str, str] = {}
    offsets: dict[str, int] = {}
    for svc in discover_pids():
        fp = os.path.join(log_dir, f"{svc}.log")
        if os.path.exists(fp):
            files[svc] = fp
            offsets[fp] = os.path.getsize(fp)  # start at EOF -> only new lines

    while not stop.is_set():
        for svc, fp in files.items():
            try:
                size = os.path.getsize(fp)
                if size < offsets.get(fp, 0):
                    offsets[fp] = 0  # rotated
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(offsets.get(fp, 0))
                    chunk = f.read()
                    offsets[fp] = f.tell()
                lines = chunk.splitlines()
                i = 0
                while i < len(lines):
                    line = _ANSI.sub("", lines[i])  # strip colour codes before parsing
                    lvl = None if _STACK.match(line) else _classify(line)
                    if lvl is not None:
                        parts = [line.strip()]
                        j = i + 1
                        # attach the following stack frames (keeps the business frame)
                        while j < len(lines) and len(parts) < 40:
                            nxt = _ANSI.sub("", lines[j])
                            if not _STACK.match(nxt):
                                break
                            parts.append(nxt.strip())
                            j += 1
                        _append(config.LOG_CSV, [_ts(), svc, lvl, " ".join(parts)[:2000],
                                                 "normal", ""])
                        i = j
                    else:
                        i += 1
            except Exception:
                pass
        stop.wait(interval)
