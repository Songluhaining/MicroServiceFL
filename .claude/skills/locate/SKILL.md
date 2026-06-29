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
and a symptom. Everything else you derive with the `fl_*` tools and by reading
the yudao source.

## Inputs you need (ask only if missing)

1. **Time window** (fault start/end, ISO-8601 UTC `…Z`). If the user gives a
   single time, use a window around it (±a few minutes).
2. **Symptom** — slow? errors/exceptions? which URL or service, if known.

Do **not** ask for anything you can find with the tools.

## Tools

| tool | use |
|------|-----|
| `fl_scan_services` | rank services by error/latency lift vs baseline — the candidate set |
| `fl_topology` | caller→callee RPC edges with per-edge latency/errors — **root vs victim** |
| `fl_endpoint_anomaly` | within a service, the slow/failing Entry endpoint |
| `fl_span_errors` | error spans grouped by exception type, **with stack** → class+method |
| `fl_error_logs` | ERROR/EXCEPTION logs incl. `logger` (class FQN) + stack |
| `fl_map_endpoint` | endpoint → module/jar/service + a grep recipe for the controller |
| `fl_class_to_jar` | class FQN → module + business jar |
| Read / Grep / Glob | read the yudao source to bridge endpoint → controller → service method |

## Method — follow this chain

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
4. **Get to the class/method.**
   - **Exception faults:** `fl_span_errors` (and `fl_error_logs`) on the root
     service. The exception type + the **top business frame** of the stack
     (`cn.iocoder.yudao.module.<m>...Impl.<method>`) *is* the class and method.
     The log `logger` is the class FQN directly.
   - **Delay faults:** `fl_map_endpoint` on the slow endpoint to get module/jar
     and a grep recipe, then **read the yudao source**: find the controller
     method for the route, follow its call into the `*.service.*Impl` class, and
     identify the method. The slowest leaf (DB/RPC/lock) inside it is the cause.
5. **Confirm the jar.** `fl_class_to_jar` on the class → `yudao-module-<m>-biz`.
   A non-business (framework/starter) class means the fault is outside a module
   jar — say so.
6. **Root cause.** Read the suspect method. For delay: which call is slow (a
   specific SQL, a Feign call, a lock, an external dependency). For exception:
   why the stack frame throws (null deref, bad cast, validation, etc.).
7. **Fix.** Propose a concrete, minimal change at that method (guard the null,
   add an index / fix the slow query, add a timeout / circuit breaker, fix the
   lock scope, …). Reference the file/line you read.

## yudao naming conventions (deterministic)

- service `yudao-mall-trade` ↔ module `trade` ↔ jar `yudao-module-trade-biz`
  ↔ package `cn.iocoder.yudao.module.trade.*`
- Feign RPC endpoint `/rpc-api/<module>/...` is a call **to** the `<module>`
  service. Entry endpoint `GET:/admin-api/<module>/...` is served **by** it.

## Output — always end with this JSON block

```json
{
  "root_service": "yudao-...",
  "fault_jar": "yudao-module-...-biz",
  "fault_class": "cn.iocoder.yudao.module....Impl",
  "fault_method": "...",
  "fault_type": "delay | exception",
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
