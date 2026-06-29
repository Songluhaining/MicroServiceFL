# MicroServiceFL

A fine-grained fault-localization agent for the **yudao** Spring Cloud
microservice system, built on OpenHarness. Given an incident (a time window + a
symptom), it localizes the fault to **service → jar → class → method** from the
collected observability data and proposes a fix.

It is an add-on package: the harness core is untouched except a guarded
registration of the `fl_*` tools, so it stays upgradable against upstream.

## How it works

```
incident (time + symptom)
   │
   ▼
/locate skill  ──drives──▶  LLM agent (local DeepSeek)
   │                              │ uses
   │                              ▼
   │                   fl_* tools  ──query──▶  DataSource ──▶ DuckDB (metric/trace/log)
   │                   Read/Grep   ──read───▶  yudao source tree
   ▼
service → jar → class → method  +  root cause  +  fix   (structured JSON)
```

| layer | where | role |
|-------|-------|------|
| data contract | `SCHEMA.md` | the metric/trace/log schema the agent consumes |
| ingest | `ingest.py` | collected CSVs → DuckDB (handles the 8.5 GB trace) |
| data access | `datasource/` | stable `DataSource` interface + DuckDB impl |
| tools | `tools/` | 9 `fl_*` BaseTools (signals, topology, code mapping, cases) |
| methodology | `../.claude/skills/locate/SKILL.md` | the RCA playbook + output contract |
| eval | `../scripts/eval_fault_localization.py` | score service/jar/class/method accuracy |
| demo data | `synthetic.py` | signal-present dataset for offline validation |

The localization signal map (which field answers which granularity) is in
`SCHEMA.md`. Short version: metrics/traces give the service; Feign topology
separates root cause from victims; trace error stacks and log `logger` give the
class/method for **exception** faults; **delay** faults reach class/method by
mapping the slow endpoint to the yudao source.

## Setup

```bash
# 1. install the harness + this add-on's deps (duckdb, pandas)
pip install -e ".[fl]"            # or: uv sync --extra fl

# 2. point a local DeepSeek (vLLM / SGLang / Ollama, OpenAI-compatible) at oh
oh provider add deepseek-local \
  --label "Local DeepSeek" --provider deepseek \
  --api-format openai --auth-source openai_api_key \
  --model deepseek-v3 \
  --base-url http://YOUR_SERVER:8000/v1
oh provider use deepseek-local
```

Use **DeepSeek-V3** as the driver (reliable tool-calling, which the agent leans
on heavily). R1-style reasoning models can be flaky with multi-tool loops; if
you want R1, reserve it for the final root-cause/fix reasoning step.

## Quickstart (on the demo dataset, no re-collection needed)

```bash
# build a small signal-present dataset
python -m microservice_fl.synthetic --db ./fl_demo.duckdb
export OH_FL_DB=$PWD/fl_demo.duckdb          # Windows: set OH_FL_DB=...\fl_demo.duckdb

# non-LLM baseline / data sanity check (service & jar 100%, exceptions to method)
python scripts/eval_fault_localization.py --predictor heuristic

# the real agent (needs DeepSeek configured)
oh
> /locate time=2026-07-01T00:04:00Z~00:07:00Z symptom=mail-account delete-list 很慢
```

## On your real collected data

```bash
# put the CSVs where config.py expects them (or set OH_FL_DATASET_DIR), then:
export OH_FL_DATASET_DIR=/path/to/dataset
python -m microservice_fl.ingest            # builds <dataset>/fl.duckdb
export OH_FL_DB=/path/to/dataset/fl.duckdb
```

For class/method localization the agent reads the yudao source — clone it into
the working directory so Read/Grep/Glob can navigate controller→service:

```bash
git clone https://gitee.com/yudaocode/yudao-boot-mini
```

### Data quality matters more than the model

A localizer can only find a fault that left a signal. Before trusting results,
confirm the collection actually captured the injected effect (the original
reference set did not — see `SCHEMA.md`). After collecting, sanity-check:

- a `delay` fault's endpoint P99 in the fault window ≫ baseline (and ≳ the
  injected delay);
- an `exception` fault produces error spans / EXCEPTION logs with a stack in the
  fault window.

`python scripts/eval_fault_localization.py --predictor heuristic` is the quick
gate: if even the heuristic can't get service/jar, the data lacks signal.

## Extending to real-time

Everything routes through the `DataSource` interface. To go from offline CSVs to
live triage, implement `DataSource` against SkyWalking OAP / Prometheus / Loki
and construct it in `datasource/__init__.get_default_source`. No tool, skill, or
agent code changes. The trigger can then be an alert webhook that runs `/locate`
and posts the result (e.g. via the ohmo gateway to Feishu/Slack).

## Configuration (env vars)

| var | default | meaning |
|-----|---------|---------|
| `OH_FL_DATASET_DIR` | `E:\Myself\赛宝实习\dataset` | raw CSV directory |
| `OH_FL_DB` | `<dataset>/fl.duckdb` | DuckDB database the tools query |
