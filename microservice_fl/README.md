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

## Install

One-click (creates the venv, installs `.[fl]`, fetches CFR, runs `fl doctor`):

```powershell
.\install-fl.ps1        # Windows       (bash: ./install-fl.sh)
```

Or manually: `pip install -e ".[fl]"` (or `uv sync --extra fl`).

Then point a DeepSeek at the agent — the official API:

```bash
export DEEPSEEK_API_KEY=sk-...
oh provider use deepseek        # built-in profile -> https://api.deepseek.com
```

or a locally-hosted DeepSeek (vLLM/SGLang/Ollama, OpenAI-compatible):

```bash
oh provider add deepseek-local --provider deepseek --api-format openai \
  --auth-source openai_api_key --model deepseek-v3 --base-url http://YOUR_SERVER:8000/v1
oh provider use deepseek-local
```

Use **DeepSeek-V3** (`deepseek-chat`) as the driver — reliable tool-calling.
R1-style models can be flaky with multi-tool loops.

## The `fl` command

One entry point (`python -m microservice_fl` or `fl` after install):

```bash
fl doctor                     # check build + run readiness
fl targets                    # list target-system profiles, show the active one
fl build-index --jars <dir>   # deployed jars -> endpoint_index.json (localization)
fl ingest --dataset <dir>     # collected CSVs -> fl.duckdb (offline mode)
fl init --jars <dir> --data <dir>   # both, in one step
fl repl                       # interactive localization (watch each tool call)
fl locate "time=... symptom=..."    # one-shot
```

### Onboarding a different system

The naming conventions (service→module→jar→package, API prefixes) live in a
**target profile**, not code. yudao-cloud is the default. For another Spring
Cloud system, copy `microservice_fl/targets/yudao-cloud.example.json`, edit the
mappings, drop it at `~/.openharness/fl_targets/<name>.json`, and select it with
`OH_FL_TARGET=<name>` — no code change.

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

## Live mode (no ingest, never stale)

Everything routes through the `DataSource` interface, so live triage needs **no
tool, skill, or agent changes** — only a different data source. Set
`OH_FL_DATASOURCE=skywalking` and the tools query the incident window on demand:

```bash
export OH_FL_DATASOURCE=skywalking
export OH_FL_SKYWALKING_URL=http://YOUR_OAP:12800/graphql   # trace / topology / errors
export OH_FL_METRIC_CSV=/path/to/metric.csv                 # live psutil CSV (cpu/mem)
export OH_FL_LOG_CSV=/path/to/log.csv                       # live log CSV (error logs)
```

`SkyWalkingDataSource` routes each modality to where it actually lives: **trace**
signals (endpoint anomalies, topology, error spans, endpoint breakdown) come from
OAP's GraphQL API; **metric** (cpu/mem) and **log** come straight from the
continuously-appended collector CSVs, window-filtered via a transient DuckDB
`read_csv` — always fresh, no ingest. Logs return empty gracefully if the CSV is
absent. The trigger can be an alert webhook that runs `/locate` and posts the
result (e.g. via the ohmo gateway to Feishu/Slack).

> SkyWalking's GraphQL schema shifts between major versions; the queries target
> 9.x/10.x and are centralized in `datasource/skywalking_source.py::_Q`. If your
> OAP rejects a query, adjust them there and smoke-test one call
> (`service_anomalies` over a recent window) before running `/locate`.

## Configuration (env vars)

| var | default | meaning |
|-----|---------|---------|
| `OH_FL_DATASET_DIR` | `E:\Myself\赛宝实习\dataset` | raw CSV directory |
| `OH_FL_DB` | `<dataset>/fl.duckdb` | DuckDB database the tools query (offline) |
| `OH_FL_INDEX` | `<dataset>/endpoint_index.json` | grey-box endpoint→class index |
| `OH_FL_JARS` | `E:\Myself\赛宝实习\yudao-cloud` | deployed jars, for decompilation |
| `OH_FL_CFR` | `~/tools/cfr-0.152.jar` | CFR decompiler jar |
| `OH_FL_DATASOURCE` | `duckdb` | `duckdb` (offline) or `skywalking` (live) |
| `OH_FL_SKYWALKING_URL` | `http://127.0.0.1:12800/graphql` | OAP GraphQL endpoint (live) |
| `OH_FL_METRIC_CSV` | `<dataset>/metric.csv` | live cpu/mem CSV (live mode) |
| `OH_FL_LOG_CSV` | `<dataset>/log.csv` | live log CSV (live mode) |
