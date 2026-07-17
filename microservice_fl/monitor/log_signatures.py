"""Content-aware log-error detection: fire on a NEW error *signature*, not a count.

The ``error_count`` series catches "errors got more frequent". This complements it
by catching "a new KIND of error appeared" — a low-volume but important error (a
fresh NullPointerException, a missing table) that may never move the count enough
to breach a 3-sigma threshold, yet clearly matters.

A **signature** is ``(exception type, top yudao business frame)``, taken from the
error line with ANSI colour codes stripped and numbers/quoted names normalised, so
the same fault collapses to one signature regardless of ids/timestamps.

Policy (plan A): during a **warmup** the detector LEARNS the signatures already
present — pre-existing, known issues (e.g. a module whose tables aren't imported)
become the baseline and do **not** alert. After warmup, any signature not in that
baseline is *novel* and is surfaced; the ``watch`` loop's edge-trigger + cooldown
then dedup it to "alert once per incident". Critical patterns (OOM, deadlock,
missing table, ...) get a score boost so they win the per-tick priority.

Benign client-side noise (request-validation failures) is whitelisted out — it is
neither learned nor fired.
"""

from __future__ import annotations

import re
from collections import defaultdict

#: terminal colour escape sequences that logback console layouts leak into files
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: a Java exception/error class token anywhere in the line
_EXC = re.compile(r"([A-Za-z_][\w.]*(?:Exception|Error))")
#: the first yudao business frame in the attached stack (the class.method that threw)
_FRAME = re.compile(r"at (cn\.iocoder\.yudao\.[\w.$]+)\(")
_NUM = re.compile(r"\d+")
_QUOTED = re.compile(r"'[^']*'")

#: exception types that are almost always benign client-side noise (400-class):
#: they are skipped entirely — not learned, not fired.
_WHITELIST = {
    "MethodArgumentNotValidException",
    "ConstraintViolationException",
    "BindException",
    "HttpMessageNotReadableException",
}
#: patterns that should win the per-tick priority the moment they first appear
_CRITICAL = re.compile(
    r"OutOfMemoryError|StackOverflowError|Deadlock|Connection refused|"
    r"doesn't exist|Too many connections|Broken pipe|Communications link failure",
    re.IGNORECASE,
)


def _clean(msg: str) -> str:
    return _ANSI.sub("", msg or "")


def signature(level: str, message: str) -> tuple[str, str] | None:
    """Return ``(exc_type, anchor)`` for an error line, or ``None`` to skip it.

    ``anchor`` is the top yudao business frame when the stack carries one, else the
    normalised message head (numbers → ``N``, quoted names → ``'X'``) so the same
    error collapses to one signature.
    """
    msg = _clean(message)
    m = _EXC.search(msg)
    exc = m.group(1).split(".")[-1] if m else (level or "ERROR")
    if exc in _WHITELIST:
        return None
    fr = _FRAME.search(msg)
    if fr:
        anchor = fr.group(1)
    else:
        anchor = _QUOTED.sub("'X'", _NUM.sub("N", msg[:80])).strip()
    return exc, anchor


class SignatureDetector:
    """Track per-service error signatures; surface novel ones after a warmup."""

    def __init__(self, *, warmup: int = 15) -> None:
        #: windows to learn the baseline signature set before firing on anything
        self.warmup = warmup
        self._ticks = 0
        self._known: dict[str, set[tuple[str, str]]] = defaultdict(set)

    def update(self, logs) -> list[dict]:
        """Feed the window's ERROR/EXCEPTION ``LogEntry`` list; return anomaly dicts.

        Anomaly dicts are shaped like the metric anomalies the ``watch`` loop already
        handles (``key/service/metric/value/score/fault_hint`` ...), so no downstream
        change is needed. During warmup this only *learns* signatures and returns
        ``[]``; afterwards it returns one dict per novel ``(service, signature)``.
        """
        self._ticks += 1
        learning = self._ticks <= self.warmup

        seen: dict[str, dict[tuple[str, str], tuple[int, str]]] = defaultdict(dict)
        for e in logs:
            sig = signature(getattr(e, "level", ""), getattr(e, "message", ""))
            if sig is None:
                continue
            svc = getattr(e, "service", "") or "(unknown)"
            n, sample = seen[svc].get(sig, (0, e.message))
            seen[svc][sig] = (n + 1, sample)

        out: list[dict] = []
        for svc, sigs in seen.items():
            for sig, (n, sample) in sigs.items():
                if sig in self._known[svc]:
                    continue
                if learning:
                    self._known[svc].add(sig)  # baseline: learn silently
                    continue
                exc, anchor = sig
                critical = bool(_CRITICAL.search(_clean(sample)))
                out.append({
                    "key": f"sig:{svc}:{exc}:{anchor}"[:160],
                    "service": svc,
                    "metric": "error_signature",
                    "value": float(n),
                    "threshold": 0.0,
                    "score": 500.0 if critical else 60.0,
                    "fault_hint": "exception",
                    "exc_type": exc,
                    "frame": anchor,
                    "sample": _clean(sample)[:300],
                })
        return out
