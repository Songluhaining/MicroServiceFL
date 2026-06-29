"""Offline evaluation for the MicroServiceFL localizer.

Replays every recorded fault case as an incident (time window + symptom, with
the ground-truth answer hidden), runs a *predictor* to localize it, and scores
the prediction at four granularities: service / jar / class / method.

Two predictors:

* ``heuristic`` (default) — pure-Python RCA over the DataSource (no LLM). Doubles
  as a sanity check that the data carries enough signal, and as a baseline.
* ``oh-cli`` — shells out to the ``oh`` agent headless with the ``/locate`` skill
  and parses the JSON it emits. Requires a configured provider (e.g. local
  DeepSeek). This is the real agent under test.

Usage::

    set OH_FL_DB=...\\fl_demo.duckdb
    python scripts/eval_fault_localization.py --predictor heuristic
    python scripts/eval_fault_localization.py --predictor oh-cli --limit 20
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from microservice_fl import config  # noqa: E402
from microservice_fl.datasource import get_default_source  # noqa: E402
from microservice_fl.datasource.base import Case, DataSource, TimeWindow  # noqa: E402

_STACK_FRAME = re.compile(r"at\s+(cn\.iocoder\.yudao\.module\.[\w.]+)\.(\w+)\(")


def incident_for(case: Case) -> dict:
    """Build the operator-style incident (no answer fields)."""
    if case.fault_type == "delay":
        symptom = f"requests to {case.trigger_url} are slow / timing out"
    elif case.fault_type == "exception":
        symptom = f"requests to {case.trigger_url} are failing with errors"
    else:
        symptom = f"{case.fault_type} affecting {case.trigger_url}"
    return {"case_id": case.case_id, "start": case.fault_start,
            "end": case.fault_end, "symptom": symptom}


# --------------------------------------------------------------------------- #
# Heuristic predictor (no LLM) — exercises the whole tool chain
# --------------------------------------------------------------------------- #

def _heuristic_predict(src: DataSource, incident: dict) -> dict:
    w = TimeWindow(start=incident["start"], end=incident["end"])

    # 1) exception path: an error span with a real exception type wins outright.
    errs = [e for e in src.span_errors(None, w, top_n=30) if e.error_type not in ("", "unknown")]
    if errs:
        top = max(errs, key=lambda e: e.count)
        klass = method = None
        if top.sample_stack:
            m = _STACK_FRAME.search(top.sample_stack)
            if m:
                klass, method = m.group(1), m.group(2)
        return {
            "root_service": top.service,
            "fault_jar": config.class_fqn_to_jar(klass or "") or config.service_to_jar(top.service),
            "fault_class": klass,
            "fault_method": method,
            "fault_type": "exception",
        }

    # 2) delay path: root = service whose slowest *endpoint* lift is NOT explained
    #    by a downstream it calls. Using the endpoint (not the service average)
    #    avoids diluting a low-traffic faulted endpoint among normal ones; using
    #    the outgoing-edge latency separates the root from its victims.
    best_svc, best_score, best_ep = None, -1.0, None
    for s in src.service_anomalies(w, top_n=50):
        eps = src.endpoint_anomalies(s.service, w, top_n=1)
        if not eps:
            continue
        ep = eps[0]
        ep_lift = ep.avg_latency_ms - ep.baseline_avg_latency_ms
        if ep_lift <= 0:
            continue
        out_edges = src.topology(w, service=s.service)
        max_out = max((e.avg_latency_ms for e in out_edges), default=0.0)
        unexplained = ep_lift - max_out
        if unexplained > best_score:
            best_score, best_svc, best_ep = unexplained, s.service, ep.endpoint

    klass = method = None
    return {
        "root_service": best_svc,
        "fault_jar": config.service_to_jar(best_svc or ""),
        "fault_class": klass,        # delay → class/method need source reading (LLM's job)
        "fault_method": method,
        "fault_type": "delay",
        "endpoint": best_ep,
    }


# --------------------------------------------------------------------------- #
# oh-cli predictor — the real agent under test
# --------------------------------------------------------------------------- #

def _oh_cli_predict(incident: dict, *, oh_cmd: str) -> dict:
    prompt = (
        f"/locate time={incident['start']}~{incident['end']} symptom={incident['symptom']}\n"
        "Localize the fault and end with the JSON block as the skill specifies."
    )
    proc = subprocess.run(
        [oh_cmd, "-p", prompt, "--output-format", "text", "--permission-mode", "auto"],
        capture_output=True, text=True, timeout=600,
    )
    out = proc.stdout
    blocks = re.findall(r"\{[\s\S]*\}", out)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return {"root_service": None, "fault_jar": None, "fault_class": None,
            "fault_method": None, "_raw": out[-500:]}


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

@dataclass
class Score:
    service: bool
    jar: bool
    klass: bool
    method: bool


def score(case: Case, pred: dict) -> Score:
    truth_jar = config.module_to_jar(case.module)
    svc = pred.get("root_service") == case.service
    jar = pred.get("fault_jar") == truth_jar
    pc = (pred.get("fault_class") or "").strip()
    klass = pc == case.class_fqn or (bool(pc) and pc.endswith(case.class_fqn.split(".")[-1]))
    method = klass and (pred.get("fault_method") or "").strip() == case.method
    return Score(service=svc, jar=jar, klass=klass, method=method)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Evaluate the fault localizer.")
    p.add_argument("--predictor", choices=["heuristic", "oh-cli"], default="heuristic")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--oh-cmd", default="oh", help="oh executable for the oh-cli predictor")
    p.add_argument("--out", type=Path, default=None, help="optional per-case CSV path")
    args = p.parse_args(argv)

    src = get_default_source()
    cases = src.list_cases()
    if args.limit:
        cases = cases[: args.limit]

    rows = []
    agg = {"service": 0, "jar": 0, "class": 0, "method": 0}
    for case in cases:
        incident = incident_for(case)
        if args.predictor == "heuristic":
            pred = _heuristic_predict(src, incident)
        else:
            pred = _oh_cli_predict(incident, oh_cmd=args.oh_cmd)
        s = score(case, pred)
        agg["service"] += s.service
        agg["jar"] += s.jar
        agg["class"] += s.klass
        agg["method"] += s.method
        rows.append((case, pred, s))
        mark = "".join("Y" if v else "-" for v in (s.service, s.jar, s.klass, s.method))
        print(f"{case.case_id} [{mark}] truth={case.service}/{case.class_fqn.split('.')[-1]}."
              f"{case.method}  pred={pred.get('root_service')}/{pred.get('fault_class')}."
              f"{pred.get('fault_method')}")

    n = len(cases) or 1
    print("\n=== accuracy (top-1) ===")
    for level in ("service", "jar", "class", "method"):
        print(f"  {level:<8} {agg[level]:>3}/{n}  {agg[level] / n:.1%}")

    if args.out:
        import csv
        with args.out.open("w", newline="", encoding="utf-8") as fh:
            wtr = csv.writer(fh)
            wtr.writerow(["case_id", "fault_type", "truth_service", "truth_class", "truth_method",
                          "pred_service", "pred_class", "pred_method",
                          "ok_service", "ok_jar", "ok_class", "ok_method"])
            for case, pred, s in rows:
                wtr.writerow([case.case_id, case.fault_type, case.service, case.class_fqn,
                              case.method, pred.get("root_service"), pred.get("fault_class"),
                              pred.get("fault_method"), s.service, s.jar, s.klass, s.method])
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
