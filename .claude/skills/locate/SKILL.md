---
name: locate
description: Localize a yudao microservice fault to jar/class/method and propose a fix. Use when the user reports an incident — a time window plus a symptom (slow endpoint, errors, exceptions) — and wants the root-cause location and a repair suggestion. Triggers include "定位故障", "locate fault", "root cause", "哪个服务/类/方法出问题", "/locate".
argument-hint: 时间窗口 + 故障现象 (e.g. "2026-07-01T00:04:00Z~00:07:00Z crm 业务分页大量报错")
version: 0.1.0
---

# Microservice Fault Localization (yudao)

You localize a fault in the **yudao Spring Cloud** system to **jar / class /
method** granularity from the collected observability data, then explain the
root cause and propose a fix. The user gives you an **incident**: a time window
and a symptom. Everything else you derive with the `fl_*` tools.

This is **grey-box**: there is no source tree. You bridge a trace endpoint to a
class/method with `fl_map_endpoint` (an index built from the deployed jars) and
read a class's body with `fl_decompile_class` (decompiled from its jar). Never
assume a source checkout.

## Inputs you need (ask only if missing)

1. **Time window** (fault start/end, ISO-8601 UTC `…Z`). If the user gives a
   single time, use a window around it (±a few minutes).
2. **Symptom** — slow? errors/exceptions? which URL or service, if known.

Do **not** ask for anything you can find with the tools.

## Tools

| tool | use |
|------|-----|
| `fl_capabilities` | **call first** — probes if the jar is parseable and the max granularity (method/class/endpoint) |
| `fl_scan_services` | rank services by error/latency lift vs baseline — the candidate set |
| `fl_topology` | caller→callee RPC edges with per-edge latency/errors — **root vs victim** |
| `fl_endpoint_anomaly` | within a service, the slow/failing Entry endpoint |
| `fl_endpoint_breakdown` | break a slow endpoint into downstream ops (SQL/Feign/Redis) — code-free delay root cause |
| `fl_span_errors` | error spans grouped by exception type, with stack if present → class+method |
| `fl_error_logs` | ERROR/EXCEPTION logs incl. `logger` (class FQN) + stack if present |
| `fl_map_endpoint` | endpoint → module/jar/service + **exact controller class/method** (jar-built index) |
| `fl_class_to_jar` | class FQN → module + business jar |
| `fl_decompile_class` | decompile a class from its deployed jar → read its body (controller→service→impl) |

## Method — follow this chain

0. **Probe capabilities.** `fl_capabilities` first. It tells you the **maximum
   granularity** this deployment supports and whether the jar is parseable:
   - **method** — jar parseable (index + decompile): do the full chain below.
   - **class** — index only (jar encrypted/undecompilable): localize the class
     from the index, but take the **root cause from telemetry** (step 6a), not
     decompiled code.
   - **endpoint** — no jar at all: **telemetry-only**. Skip `fl_map_endpoint`
     class resolution and all decompilation; localize service/endpoint from
     trace, root cause from `fl_endpoint_breakdown`, and class/method **only** if
     an exception log carries a business frame. Report `granularity: "endpoint"`
     and do not invent a class/method you cannot support.
   Never claim a granularity finer than `fl_capabilities` reports.
1. **Scope.** `fl_scan_services` over the window. Note every service with a
   real lift (error-rate up, or latency up vs baseline).
2. **Find the root, not a victim.** `fl_topology`. In Spring Cloud a slow/failing
   *downstream* (commonly `yudao-system` for auth, `yudao-infra` for logging)
   makes **all its callers** look bad. If many services degrade together and
   they share a downstream whose inbound edges are slow/errored, the **downstream
   is the root** — the callers are victims. Pick the deepest service that is
   anomalous *on its own*, not merely because something it calls is anomalous.
3. **Pin the endpoint.** `fl_endpoint_anomaly` on the root service → the Entry
   endpoint with the biggest latency lift (delay faults) or error rate
   (exception faults).
4. **Get to the class/method** (grey-box: index + decompile, no source).
   - **Both fault kinds start the same way:** `fl_map_endpoint` on the slow /
     failing endpoint → the exact **controller class + method** and the jar
     (resolved from the jar-built index — this needs no source and no
     decompilation). To reach the `*ServiceImpl`: if the jar decompiles,
     `fl_decompile_class` the controller to see which
     `cn.iocoder.yudao.module.<m>.service.*Service` it calls, then that
     `*ServiceImpl`. If it does not decompile, infer the impl by yudao
     convention (`XxxController` → `XxxServiceImpl`, same method name) and note
     the lower class confidence.
   - **Exception faults, if the stack is rich:** `fl_span_errors` /
     `fl_error_logs` may already name the business frame
     (`...Impl.<method>`) or the log `logger` (class FQN) — use it directly.
     But do **not** rely on it: coarse telemetry often carries only framework
     frames (e.g. Spring Security), in which case fall back to the
     endpoint→index→decompile path above.
5. **Confirm the jar.** `fl_class_to_jar` on the class → `yudao-module-<m>-server`.
   A non-business (framework/starter) class means the fault is outside a module
   jar — say so.
6. **Root cause.** Decompilation is **optional refinement, not required** — the
   class/method is already localized from the index in step 4.
   - **Delay (code-free, primary):** `fl_endpoint_breakdown` on the slow endpoint
     names the dominant downstream op — a specific SQL, a Feign call, a Redis op,
     a lock. That op *is* the root cause; you can state it with no source at all.
   - **Refine if the jar decompiles:** `fl_decompile_class` the `*ServiceImpl` to
     read the method body and pin the exact line / pattern (a per-item loop /
     N+1, a missing index). If decompilation errors or returns garbage
     (obfuscated / unavailable jar), **do not fail** — keep the breakdown-based
     root cause and lower the `method`/fix confidence accordingly.
   - **Exception:** the exception type + top business frame (from `fl_span_errors`
     / `fl_error_logs`, or the decompiled method) explains why a frame throws
     (null deref, bad cast, validation).
7. **Fix.** Propose a concrete, minimal change at that method (guard the null,
   add an index / fix the slow query, add a timeout / circuit breaker, fix the
   lock scope, …). Reference the file/line you read.

## yudao naming conventions (deterministic)

- service `yudao-mall-trade` ↔ module `trade` ↔ jar `yudao-module-trade-server`
  ↔ package `cn.iocoder.yudao.module.trade.*` (the collected telemetry uses the
  `yudao-<module>` / `yudao-mall-<sub>` service names; the jars are
  `yudao-module-<module>-server`).
- Feign RPC endpoint `/rpc-api/<module>/...` is a call **to** the `<module>`
  service. Entry endpoint `GET:/admin-api/<module>/...` is served **by** it.

## Output — always end with this JSON block

Aim for **10–20 tool calls total**. Do not re-run a tool with the same arguments
you already have the answer for. Once you emit the JSON block below, **stop
immediately** — do not call more tools, and do not repeat the report.

```json
{
  "root_service": "yudao-...",
  "fault_jar": "yudao-module-...-server",
  "fault_class": "cn.iocoder.yudao.module....Impl (or null if granularity < class)",
  "fault_method": "... (or null if granularity < method)",
  "fault_type": "delay | exception",
  "granularity": "method | class | endpoint | service",
  "confidence": { "service": 0.0, "jar": 0.0, "class": 0.0, "method": 0.0 },
  "evidence": ["tool/finding 1", "tool/finding 2", "..."],
  "root_cause": "one-paragraph explanation",
  "fix_suggestion": "concrete change, ideally with file:line"
}
```

Set each confidence honestly:
- **service/jar** — high when one service clearly dominates and topology rules
  out victims.
- **class** — high for exception faults (stack/logger give it directly); for
  delay faults it depends on how cleanly the endpoint maps to one service method.
- **method** — high when the stack frame or a method-level span names it; lower
  when inferred only from endpoint→code reading.

If a modality contradicts the others, say so in `evidence` and lower confidence
rather than forcing a single answer.
