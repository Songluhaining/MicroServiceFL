# MicroServiceFL — Data Contract

This is the observability schema the fault-localization agent consumes. It is
**not** tied to the original collected CSVs: it is the *target* format that (a)
is realistically obtainable from a Spring Cloud + SkyWalking + Prometheus +
Loki/ELK deployment of yudao, and (b) carries enough signal to localize a fault
down to **jar / class / method**.

All three modalities (metric / trace / log) are assumed **present and
complete** — that is the production reality of the target deployment. The agent
cross-validates across them; it does not treat any one as optional.

## Why the original reference dataset looked empty

The first reference collection labeled fault windows over telemetry whose
**fault effect was largely absent** (injected 1–3 s delays never appeared in
spans; injected exceptions produced no error spans / no exception logs in the
labeled windows). Per the operator this was a one-off collection-time issue:
memory/storage pressure truncated the log and/or trace modality. It does **not**
reflect what production collection yields. The synthetic generator
(`microservice_fl/synthetic.py`) produces a small, signal-present dataset in
exactly the schema below so the whole pipeline can be validated offline without
waiting on a re-collection.

## Tables

### `trace` — distributed spans (SkyWalking)

| column | type | required | notes |
|--------|------|----------|-------|
| `timestamp` | ISO-8601 `...Z` | ✓ | span emit time |
| `trace_id` | string | ✓ | correlation id (also stamped on logs) |
| `service` | string | ✓ | e.g. `yudao-crm` |
| `endpoint` | string | ✓ | Entry: `GET:/admin-api/...`; Feign Exit: `/rpc-api/<module>/...` |
| `span_id` | int | ✓ | per-segment |
| `parent_span_id` | int | ✓ | `-1` for segment root |
| `span_start` / `span_end` | epoch ms | ✓ | |
| `span_duration` | int (ms) | ✓ | **carries delay-fault signal** |
| `span_type` | `Entry`/`Exit`/`Local` | ✓ | |
| `peer` | string | – | downstream `ip:port` for Exit spans |
| `component` | string | ✓ | `Tomcat`/`Feign`/`AlibabaDruid`/`Redisson`/`mysql-connector-java`/... |
| `is_error` | bool | ✓ | **carries exception-fault signal** |
| **`error_type`** | string | ★ new | exception FQN when `is_error` (e.g. `java.lang.NullPointerException`) |
| **`error_stack`** | string | ★ new | top frames of the stack — **the bridge to class/method** |
| **`operation_class`** | string | ◆ ideal | when method-level tracing is on: the bean class behind the span |
| **`operation_method`** | string | ◆ ideal | the method behind the span — direct method-level localization |

★ = the cheap, high-value additions (SkyWalking error tags + MDC). ◆ = ideal if
method-level tracing is enabled on `*.service.impl` beans; otherwise the agent
infers method from `endpoint` via the yudao source.

### `log` — application logs (Loki/ELK)

| column | type | required | notes |
|--------|------|----------|-------|
| `timestamp` | ISO-8601 `...Z` | ✓ | |
| `service` | string | ✓ | |
| `level` | `INFO`/`WARN`/`ERROR`/`EXCEPTION` | ✓ | |
| `message` | string | ✓ | |
| **`logger`** | string | ★ new | logging class FQN, e.g. `cn.iocoder.yudao.module.crm...CrmBusinessServiceImpl` — **direct class signal** |
| **`trace_id`** | string | ★ new | MDC trace id, lets the agent join a log line to its request |
| **`thread`** | string | – | |
| **`stack_trace`** | string | – | full stack for EXCEPTION lines |

### `metric` — per-service resource/RED metrics (Prometheus)

| column | type | required | notes |
|--------|------|----------|-------|
| `timestamp` | ISO-8601 `...Z` | ✓ | |
| `service` | string | ✓ | `_system_` for host-level rows |
| `level` | `host`/`process` | ✓ | |
| `cpu_pct` / `mem_pct` / `proc_cpu_pct` / `proc_mem_pct` | float | – | resource saturation |
| **`req_rate`** / **`error_rate`** / **`p99_latency_ms`** | float | ◆ ideal | RED metrics per service — cheap from SkyWalking/Micrometer |

### `ground_truth` — eval labels only (never shown to the agent at inference)

`case_id, fault_start, fault_end, fault_type, service, module, class_fqn,
method, param, trigger_url`

### `phase_timeline` — optional normal/fault/recovery phase annotation (eval aid)

## Localization signal map (which field answers which granularity)

| granularity | primary signal | fallback |
|-------------|----------------|----------|
| **service** | `metric.error_rate`/`p99` lift, `trace` Entry error/latency lift | log error bursts |
| **root vs victim** | `trace` Feign topology (caller→callee edge latency/errors) | — |
| **endpoint** | `trace` Entry `endpoint` latency/error lift | — |
| **jar** | `class_fqn`→`yudao-module-<m>-biz` (deterministic) | endpoint→module |
| **class** | `log.logger` / `trace.error_stack` / `operation_class` | endpoint→controller→service (read yudao source) |
| **method** | `operation_method` / stack top frame | endpoint→service method (read yudao source) |
