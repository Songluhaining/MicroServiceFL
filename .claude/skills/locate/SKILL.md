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
6. **Root cause — state only what the evidence supports; the *type* of fault
   decides how deep you can honestly go.** Do **not** invent a code-level story
   the telemetry contradicts (e.g. never claim "full table scan" when the SQL
   span was fast). Localization (class/method) and root-cause *explanation* are
   separate: you may know the method yet not have a code-level cause.
   - **Delay — first do the latency accounting.** `fl_endpoint_breakdown` reports
     downstream ops **and** the "in-method (unaccounted)" time.
     - If a **downstream op dominates** (a specific slow SQL / Feign / lock / Redis)
       → that op is the root cause; name it (code-free is fine).
     - If the time is **in-method / unaccounted** (downstream ops are all fast) →
       the delay is in the method's **own execution**: a blocking call, a sleep, a
       lock, a CPU-bound loop — **or an injected/artificial delay**. Say exactly
       that; do **not** pin it on a downstream call the breakdown shows is fast.
       Only decompile to look for an obvious in-method cause (tight loop, sleep);
       if the code shows nothing that explains 3s, say the cause is not visible in
       code (likely external/injected) rather than fabricating one.
   - **Exception — the cause is real and code-level.** The exception type + the
     top business frame (from `fl_error_logs` stack / `fl_span_errors` / the
     decompiled method) is *why* it throws (null deref, bad cast, validation).
   - **Resource (cpu/mem) — service-level.** Name the saturated service; the
     "method" may be null. Don't force a class/method for a whole-service resource
     fault.
   - Decompilation is **optional refinement**. If it errors / is obfuscated,
     keep the telemetry-based cause and lower confidence — don't fail.
7. **Fix — match it to the cause you actually established.** When the cause is a
   concrete code/data issue (a slow query → add an index; a null deref → guard
   it; an N+1 → batch it), propose that minimal change with the file/line. When
   the delay is **in-method / unaccounted and no code reason is visible** (or the
   jar can't be read), do **not** fabricate a code fix — recommend the honest
   next step instead (profile the method / check for an external or injected
   delay / add a timeout) and keep `fix_suggestion` proportional to your
   confidence.

## yudao naming conventions (deterministic)

- service `yudao-mall-trade` ↔ module `trade` ↔ jar `yudao-module-trade-server`
  ↔ package `cn.iocoder.yudao.module.trade.*` (the collected telemetry uses the
  `yudao-<module>` / `yudao-mall-<sub>` service names; the jars are
  `yudao-module-<module>-server`).
- Feign RPC endpoint `/rpc-api/<module>/...` is a call **to** the `<module>`
  service. Entry endpoint `GET:/admin-api/<module>/...` is served **by** it.

## Output — concise, ranked, with a confidence per candidate

Keep it **short and direct** — an operator reads this. Aim for **10–20 tool
calls**; don't re-run a tool you already have the answer from. Output exactly the
three parts below, then **stop** (no more tools, no repeated narrative).

**1. Verdict** — one line: the top candidate.
`Verdict: <service> / <class>.<method> — <fault_type> (confidence 0.NN)`

**2. Candidates** — a small ranked table. List **1–3** competing hypotheses, each
with its own confidence (they compete, so they roughly sum to ≤ 1). If one
clearly dominates it still gets its own row; if you're genuinely torn, split the
confidence. One short evidence-based reason each — no fabrication.

```
| # | location (service / class.method or endpoint) | type | confidence | why (evidence) |
|---|-----------------------------------------------|------|-----------|----------------|
| 1 | yudao-system / MailAccountServiceImpl.getMailAccountList | delay | 0.70 | p95 3014ms 150σ; breakdown: downstream ~26ms, ~2988ms in-method → blocking/sleep/lock or injected |
| 2 | yudao-system → yudao-infra (async access-log Feign)      | delay | 0.20 | 20 concurrent Feign calls in the path |
| 3 | yudao-system (Druid pool)                               | resource | 0.10 | getConnection 21× lift |
```

**3. Machine block** — end with this JSON (the top candidate expanded):

```json
{
  "verdict": "<service> / <class>.<method> — <fault_type>",
  "granularity": "method | class | endpoint | service",
  "candidates": [
    {
      "rank": 1,
      "service": "yudao-...",
      "jar": "yudao-module-...-server",
      "class": "cn.iocoder.yudao.module....Impl (or null if granularity < class)",
      "method": "... (or null if granularity < method)",
      "fault_type": "delay | exception | resource",
      "confidence": 0.0,
      "root_cause": "<= 1 sentence, evidence-based (no fabrication)",
      "evidence": ["signal 1", "signal 2"]
    },
    { "rank": 2, "...": "..." }
  ],
  "fix": "concrete next step for rank 1, matched to the established cause"
}
```

Confidence rules: high only when the evidence pins it (one service dominates and
topology rules out victims; an exception stack names the frame; a downstream op
clearly owns the latency). Lower it when a level is inferred (delay endpoint→code
without a method-level span) or a modality is missing. If the delay is
in-method/unaccounted and no code cause is visible, the rank-1 confidence in the
*cause* should be modest even if the *location* is certain — say so.
