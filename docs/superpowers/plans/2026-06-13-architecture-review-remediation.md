# Architecture Review Remediation Plan (P1–P3)

**Date:** 2026-06-13
**Source:** Multi-agent architecture review of the four subsystems (orchestrator+agents,
services, tool/MCP+data, A2UI+frontend), reviewed across four dimensions
(architecture & coupling, correctness & resilience, security, performance & tests).
**Scope of this doc:** the **P1/P2/P3** findings. The **P0** findings are already
implemented (see "Completed" below) and live on this branch.

> **How to read this doc.** Section 3 ("Cross-cutting themes") is the *execution* view —
> findings that share a fix and should be batched into one change. Section 4 is the
> *reference* catalog — every finding with a stable ID, location, problem, and fix.
> Section 5 is a suggested sequence. Each finding has an effort tag: **S** (<½ day),
> **M** (½–1 day), **L** (multi-day).

---

## 1. Status

| Priority | Count | State |
|----------|-------|-------|
| P0 | 4 | ✅ Implemented + reviewed on this branch |
| P1 | 9 | 📋 Planned (this doc) |
| P2 | 12 | 📋 Planned (this doc) |
| P3 | 11 | 📋 Planned (this doc) |

The P0 work closed the **write-authorization boundary** plus a trace-redaction leak and a
cross-tenant trigger bug. The remaining findings are correctness hardening, architectural
debt (god objects, dependency inversions), and test-coverage gaps — none are known
exploitable-now security holes, but several (SVC-P2-1 fail-open risk, TOOL-P1-1 non-fatal
validation, TOOL-P2-2 oauth scoping) are security-adjacent and should land early.

## 2. Completed (P0 — context only, already on this branch)

- **ORCH-P0-1** — Capability scope enforced at tool execution (`agent_loop.py`); `can_use_tool` was dead code.
- **ORCH-P0-2** — Raw tool output redacted before persisting to trace spans (`agent_loop.py`).
- **SVC-P0-1** — Empty-capability steps fail-closed instead of auto-executing ungated (`graph_executor.py`).
- **SVC-P0-2** — `_evaluate_triggers` workspace-scoped; empty-`workspace_id` fail-safe guard (`event_processor.py`).

Adversarial review of the P0s rejected one false-positive bypass (perception path is
tool-less, not an open gate) and added two hardening tweaks (fail-closed `db_factory`
fallback; empty-`workspace_id` guard). See branch history.

### Error handling (completed — separate user-reported issue, not from the review)

Internal exception detail (raw `str(e)`, DSNs) was reaching the frontend via SSE, WS,
REST, and execution surfaces. Root cause: no error boundary (zero exception handlers, no
internal-vs-safe message split). Fixed on this branch:

- **Boundary**: `src/errors.py` (domain `JarvisError` hierarchy + `{error:{code,message,correlation_id}}`
  envelope + `safe_error_event`) and `src/api/error_handlers.py` (four handlers; catch-all
  guarantees no raw exception escapes REST). `correlation_id` reuses the `TracingMiddleware`
  request id (minted per-connection for WS).
- **Channels swept**: SSE/WS/orchestrator stream, REST routes (auth `str(e)` → domain
  exceptions; envelope standardized), service-surfaced error fields, frontend consumers
  (`lib/api-error.ts`).
- **Review-found leaks fixed**: `run.error` written with `str(exc)` in `resume_run`
  (`graph_executor.py`) and `_mark_run_failed_after_resume` (`routes_approvals.py`) — both
  served verbatim by the history API — now store safe fields only; WS `HTTPException` frames
  and tool-failure results no longer carry raw detail.
- All catch sites route through `errorToMessage()` on the frontend (login/settings included).

This subsumes any future "error handling" findings; it is not tracked as a P-item below.

---

## 3. Cross-cutting themes (execution view — batch these)

The 32 findings collapse into a handful of themes. Fixing by theme is cheaper than by
subsystem because the fixes are mechanically similar and share tests.

### Theme A — Fail-open → fail-closed on the autonomous path  *(security-adjacent, do first)*
The P0s removed the worst fail-open gaps; these are the remainder.
- **SVC-P2-1** RiskAssessor defaults to `medium` on LLM/JSON failure → an `autonomous`
  capability auto-executes during an assessment outage. Default to `high` (forces approval).
- **TOOL-P1-1** `validate_registry()` logs errors but boots anyway → malformed registry
  reaches prod. Raise unless `JARVIS_SKIP_REGISTRY_VALIDATION`.
- **TOOL-P1-3** `validate_registry` Check 4 guards an impossible `critical` state while
  real high-risk auto-execute tools go unchecked. Extend to high-risk write tools.
> Batch as one "fail-closed hardening" change. Effort: **M** total.

### Theme B — Directional dependency inversions  *(architecture, mechanical)*
Services and assessors import *upward*; contracts live in `orchestrator`.
- **SVC-P1-2** `worker.py`, `scheduler.py`, `notifier.py`, `evidence_bundle.py` import
  `src.api.*` (the topmost layer). Move `resolve_workspace_id` + `command_context` schema
  into a service-level module; have `api.deps` re-export.
- **SVC-P1-4** `risk_assessor.py` + `relevance_assessor.py` duplicate `_get_haiku_model()`
  and import `MODEL_TIERS` from `orchestrator.jarvis`. Move model-tier constants to `src/config/`.
- **SVC-P2-3** Shared contracts (`PolicyDecision`, `SurfaceUpdate`, `StepResult`, …) live
  under `orchestrator.contracts`, forcing ~12 upward imports. Relocate to a neutral
  `src/contracts/` package both layers import downward.
> Batch as one "dependency direction" change (move shared symbols to neutral homes).
> Do SVC-P1-4 and SVC-P2-3 together (both relocate constants/contracts). Effort: **M–L**.

### Theme C — Frozen god objects accreting behavior  *(architecture, higher risk — characterization tests first)*
Per engineering-standards.md these are grandfathered but "must not grow."
- **ORCH-P1-1** `jarvis.py` (~3455 lines) — duplicated `process_message` /
  `process_message_stream` orchestration. Extract a `StepExecutor`/`ChatPipeline`; fold
  streaming/non-streaming into one core + thin adapter.
- **SVC-P1-3** `graph_executor.py` (~2026 lines) — extract `StepGate`, `SurfaceEmitter`,
  and a single `_run_with_timeout()` to collapse the duplicated execution body.
- **SVC-P2-2** `surface_detail_builders.py` (1610), `scheduler.py` (1215),
  `memory_service.py` (1142) — split by responsibility.
- **TOOL-P2-4** `intelligence_server.py` (1214) — split by domain (search/memory/planning/observation).
- **ORCH-P3-3** Extract `_handle_*` capability handlers out of `jarvis.py` into a
  `SystemCapabilityHandler` (`test_system_capability_handler.py` already exists).
> **Do NOT batch.** Each is its own change, characterization-tests-first, structure and
> behavior in separate commits (engineering-standards §refactoring). Effort: **L** each.

### Theme D — Test-coverage gaps
- **UI-P1-2** No frontend tests for the A2UI renderer / store merge / approval components.
- **TOOL-P3-3** No test for tool-vs-capability risk monotonicity.
- (P0 added enforcement + redaction tests; ORCH still lacks circuit-breaker half-open
  concurrency and thinking-fallback double-strip tests — see ORCH-P3 notes.)
> Effort: **M** (frontend harness setup is the bulk).

### Theme E — Silent-failure / swallowed-error cleanup
- **UI-P2-1** `surface_mapping.py:174` blanket `except (JSONDecodeError, Exception)` →
  narrow to `(JSONDecodeError, ValidationError)`.
- **ORCH-P3-1** `MODEL_PRICING` fallback silently mis-prices unknown models → log warning.
- **ORCH-P3-4** Audit hook always logs `tokens_used=0` → wire it or drop the column.
> Effort: **S** each.

---

## 4. Findings catalog (reference view)

### 4.1 Orchestrator + Agents

| ID | Pri | Dim | Location | Problem → Fix | Effort |
|----|-----|-----|----------|---------------|--------|
| ORCH-P1-1 | P1 | Arch | `jarvis.py` (whole) | God object ~3455 lines; duplicated streaming/non-streaming orchestration. → Extract `StepExecutor`/`ChatPipeline`; one core + non-streaming adapter. | L |
| ORCH-P1-2 | P1 | Correctness | `agent_loop.py:109-126` | `_api_call_with_retry` has an unsound contract — implicit `None` fall-through if retries misconfigured; callers assume non-None. → Add explicit `raise RuntimeError("retry loop exhausted")` after the loop. | S |
| ORCH-P1-3 | P1 | Security/Arch | `jarvis.py:789-834`, `1152-1207` | Chat path executes write steps with no TrustEngine gate. **By design** (user message = authorization); ORCH-P0-1 scope enforcement is the compensating control. → No code change required; **document** the design + the compensating control in CLAUDE.md so it isn't "fixed" by mistake. Optionally gate write steps whose triggering content came from a perception source rather than the direct user turn. | S (doc) / M (opt) |
| ORCH-P2-1 | P2 | Perf/Correctness | `agent_loop.py:360-373,508-533` | Per-tool token attribution splits tokens evenly across tool_use blocks AND a second full `record_usage` runs for the same round → token columns double-counted in any token-based aggregation (cost_usd unaffected). → Tag per-tool rows distinctly and exclude from token aggregates, or attribute proportionally. | M |
| ORCH-P2-2 | P2 | Arch | `contracts.py` | No orchestrator contracts are `frozen=True` (standards mandate immutability by default). → Add `frozen=True` to boundary contracts (already copied on mutation, so low-risk). | S |
| ORCH-P2-3 | P2 | Correctness | `intent_classifier.py:259-262` | Intent JSON parse uses naive `index`/`rindex`; breaks on prose with stray braces. → Route through the robust `parse_llm_json`/brace-matcher already used by `extract_plan`. | S |
| ORCH-P2-4 | P2 | Arch | `jarvis.py:3405`, `agent_loop.py:246` | Dispatch by string `match tool.backend` / `agent_name == "governor"` sniffing instead of discriminated union. → Model backends as a discriminated type. | M |
| ORCH-P3-1 | P3 | Correctness | `budget.py:79-81` | Unknown model silently falls back to Sonnet pricing → under-bills Opus/Bedrock 5×. → `logger.warning` on fallback. | S |
| ORCH-P3-2 | P3 | Correctness | `jarvis.py:258-262` | `shutdown()` `asyncio.wait(timeout=5.0)` abandons stragglers silently. → Cancel + log leftover tasks. | S |
| ORCH-P3-3 | P3 | Arch | `jarvis.py:2964-3219` | Five `_handle_*` capability handlers are business logic on the god object. → Extract `SystemCapabilityHandler`. | M |
| ORCH-P3-4 | P3 | Correctness | `agent_loop.py:496-506` | `audit_post_tool_hook` always called with `tokens_used=0` → dead column. → Wire `_resp_*` in or drop the field. | S |

**ORCH test gaps:** circuit-breaker half-open concurrency (two probes racing) untested;
thinking-fallback double-strip path (`agent_loop.py:294-308`) untested.

### 4.2 Services

| ID | Pri | Dim | Location | Problem → Fix | Effort |
|----|-----|-----|----------|---------------|--------|
| SVC-P1-1 | P1 | Correctness | `risk_assessor.py:213-217` | Graduation math contradicts docstring + UI: an undocumented `approved>=25 and rejection_rate<0.15 → trusted` branch disagrees with the documented `<5%` rule and `_graduation_progress` (`<0.05`). → Reconcile thresholds in one place; characterization test pinning boundary cases. | M |
| SVC-P1-2 | P1 | Arch | `worker.py:11`, `scheduler.py:18`, `notifier.py:138`, `evidence_bundle.py:14` | Services import upward into `src.api` (topmost layer). → Move `resolve_workspace_id` + `command_context` schema to a service module; `api.deps` re-exports. | M |
| SVC-P1-3 | P1 | Arch | `graph_executor.py` (whole) | ~2026 lines; frozen god object with duplicated execution bodies (lines ~800-837 vs ~927-968). → Extract `StepGate`, `SurfaceEmitter`, single `_run_with_timeout()`. | L |
| SVC-P1-4 | P1 | Arch | `risk_assessor.py:27-42`, `relevance_assessor.py:18-34` | Duplicated `_get_haiku_model()` + upward import of `MODEL_TIERS` from `orchestrator.jarvis`. → Move model-tier constants to `src/config/`; share one helper. | S |
| SVC-P2-1 | P2 | Security/Correctness | `risk_assessor.py:126-137`, `graph_executor.py:991` | Risk assessment fails *open* to `medium` → autonomous capability auto-executes during an outage. → Default to `high` (or `approval_required`) on failure. | S |
| SVC-P2-2 | P2 | Arch | `surface_detail_builders.py` (1610), `scheduler.py` (1215), `memory_service.py` (1142) | Oversized grandfathered files still growing. → Split by responsibility (scheduler by tick type; surface builders by kind). | L |
| SVC-P2-3 | P2 | Arch | `trust_engine.py:20`, `governor.py:33`, `surface_builder.py:17`, `graph_executor.py:26`, +8 | Shared contracts under `orchestrator.contracts` force ~12 upward imports. → Relocate to neutral `src/contracts/`. | M |
| SVC-P2-4 | P2 | Security | `graph_executor.py:833` | `auto_execute_silent` emits no user-facing notification (by design). → Verify an audit/event record (`step.started`) still exists for silent auto-executions; add if not. | S |
| SVC-P3-1 | P3 | Arch | `graph_executor.py:839-923` | Legacy `elif not self._trust_engine:` branch duplicates ~85 lines of gate logic with hardcoded `risk_level="low"`; dead in practice. → Delete once confirmed unreachable, or convert to an assertion. | S |
| SVC-P3-2 | P3 | Arch | `trust_engine.py:163` | `_get_ceiling` returns an untyped `SimpleNamespace` default. → Return a frozen `TrustCeiling`-shaped default. | S |
| SVC-P3-3 | P3 | Security | `world_model.py:186,353,392,439`, `memory_service.py:731,903,912`, `event_processor.py:158,575` | Several PK/idempotency lookups omit `workspace_id` (safe today via globally-unique keys). → Add `workspace_id` as defense-in-depth so a future non-unique key can't cross tenants. | M |

### 4.3 Tool/MCP + Data

| ID | Pri | Dim | Location | Problem → Fix | Effort |
|----|-----|-----|----------|---------------|--------|
| TOOL-P1-1 | P1 | Correctness | `api/app.py:168-181` | `validate_registry()` errors are logged but startup proceeds → malformed registry serves traffic. → `raise` unless `skip_registry_validation`. | S |
| TOOL-P1-2 | P1 | Correctness | `tool_registry.py:38-197` | `seed_defaults` never writes `InternalToolDef.description` → `tool_definitions.description` permanently NULL (silent drift the seed-sync claims to prevent). → Include `description` on insert + divergence check. | S |
| TOOL-P1-3 | P1 | Correctness | `validation.py:72-79` | Check 4 only flags `critical` tools, but no tool is ever `critical` → high-risk auto-execute tools go unchecked. → Extend to high-risk write tools, or assert per-tool risk ≥ capability risk floor. | S |
| TOOL-P2-1 | P2 | Arch | `tool_registry.py:199-245,284-300` | 4 unused public methods (`register_tool`, `is_write_tool`, `is_blocked_tool`, `classify_risk`); `is_write_tool` encodes a questionable equivalence. → Delete or cover with tests. | S |
| TOOL-P2-2 | P2 | Security/Correctness | `oauth_token.py:30` (+migration) | Unique index `(user_id, provider)` ignores `workspace_id` → a user in two workspaces can't hold per-workspace tokens; diverges from every other table's workspace-scoped uniqueness. Confirm vs newer `users.oauth_connections`. → Make index `(workspace_id, user_id, provider)` or retire the table. | M |
| TOOL-P2-3 | P2 | Correctness | `validation.py` | No check for duplicate tool names across INTERNAL_TOOLS / EXTERNAL_TOOL_SEEDS (seed `seen` set silently skips the duplicate). → Add Check 7 asserting global name uniqueness. | S |
| TOOL-P2-4 | P2 | Arch | `intelligence_server.py` (1214) | At the hard cap; mixes ~19 tool implementations. → Split by domain. | L |
| TOOL-P3-1 | P3 | Arch | `catalog.py:264-273`, `jarvis.py:3393` | `_special` is a `server` value handled by a special-case branch before `match tool.backend`, not a backend. → Model `_special` as a backend for uniform `match`. | S |
| TOOL-P3-2 | P3 | Correctness | `catalog.py:267` | `report_governor_verdict` shares capability `internal.evaluate_policy` with another tool → breaks 1:1 tool↔capability assumption. → Dedicated capability. | S |
| TOOL-P3-3 | P3 | Correctness/Tests | `catalog.py:363-365` | Per-tool vs capability risk divergence is intentional but untested. → Characterization test asserting risk monotonicity for non-read tools. | S |

### 4.4 A2UI + Frontend

| ID | Pri | Dim | Location | Problem → Fix | Effort |
|----|-----|-----|----------|---------------|--------|
| UI-P1-1 | P1 | Correctness | `surface-card.tsx:99-235` | Card root is a `<button>` that wraps `A2UIRenderer`, which renders real `<button>`/form elements → invalid DOM, hydration warnings, possibly dropped clicks. → Make root a `<div role="button" tabIndex={0}>` with keyboard handlers, or render chrome as a sibling overlay. | M |
| UI-P1-2 | P1 | Tests | `frontend/.../a2ui/**` | No frontend tests for the recursive dispatcher, `updateSurface` live-merge, insight/execution/approval components. → Add vitest/RTL tests (dispatcher fallthrough, partial-merge semantics, approval expiry). | M |
| UI-P2-1 | P2 | Correctness | `surface_mapping.py:174` | `except (JSONDecodeError, Exception)` swallows everything at `debug`. → Narrow to `(JSONDecodeError, ValidationError)`; let unexpected exceptions propagate/log at warning. | S |
| UI-P2-2 | P2 | Correctness | `surface-store.ts:91` | `updateSurface` applies `steps` only when `length > 0` → an update that clears steps can't (stale list persists). → Use `update.steps !== undefined` guard like the other fields. | S |
| UI-P2-3 | P2 | Arch | `surface_detail_builders.py` (1610) | (Same file as SVC-P2-2.) Split by surface kind. | L |
| UI-P2-4 | P2 | Correctness | `page.tsx:50`, `surface-card.tsx:106-108` | REST→WorkspaceSurface maps missing `kind` to `"summary"` → masks contract drift. → Telemetry/log on unknown kind; keep visual fallback. | S |
| UI-P3-1 | P3 | Arch | `page.tsx:46-87`, `chat/page.tsx:31-60` | Duplicate REST+WS merge + active-first sort across two pages. → Extract `toWorkspaceSurface()` + `useMergedSurfaces()` hook. | M |
| UI-P3-2 | P3 | Security | `insight-surface.tsx:48`, `inline-approval.tsx:50-66` | Action payloads (`execute_insight`, approve/reject) trusted by `surface_id` + unbounded client `action_index`. → **Verify backend** `execute_insight` bounds-checks `action_index` and re-scopes by workspace before executing. (This was the deferred cross-subsystem item; WS auth itself is correct in `routes_ws.py:59`.) | M |
| UI-P3-3 | P3 | Perf | `surface-card.tsx:218-235`, `renderer.tsx:64` | Recursive renderer has no max-depth guard; untrusted LLM-authored `surface_data` trees render fully. → Cap recursion depth and/or validate node count in `SurfaceDataPayload`. | S |

---

## 5. Suggested sequencing

1. **Milestone 1 — Security-adjacent hardening (do first).** Theme A
   (SVC-P2-1, TOOL-P1-1, TOOL-P1-3) + TOOL-P2-2 (oauth scoping) + UI-P3-2 (verify
   `action_index` bounds-check) + ORCH-P1-3 (document the chat-path design). Mostly **S**;
   all independent; high safety value. One PR.
2. **Milestone 2 — Correctness quick wins.** ORCH-P1-2, ORCH-P2-3, TOOL-P1-2, TOOL-P2-3,
   UI-P2-1, UI-P2-2, UI-P2-4, ORCH-P3-1, ORCH-P3-2, ORCH-P3-4, SVC-P1-1. All **S**, parallelizable.
3. **Milestone 3 — Dependency direction (Theme B).** SVC-P1-2, SVC-P1-4, SVC-P2-3.
   Mechanical relocations; land together so imports settle once.
4. **Milestone 4 — Test coverage (Theme D).** UI-P1-2 (frontend harness), TOOL-P3-3,
   ORCH circuit-breaker/thinking tests.
5. **Milestone 5 — God-object extractions (Theme C).** ORCH-P1-1, SVC-P1-3, SVC-P2-2/UI-P2-3,
   TOOL-P2-4, ORCH-P3-3. Each its own change, **characterization tests first**, structure
   and behavior in separate commits. Highest risk — do last, one at a time.
6. **Cleanup.** UI-P1-1, ORCH-P2-1, ORCH-P2-2, ORCH-P2-4, TOOL-P2-1, TOOL-P3-1, TOOL-P3-2,
   SVC-P2-4, SVC-P3-1, SVC-P3-2, SVC-P3-3, UI-P3-1, UI-P3-3.

## 6. Out of scope / accepted (verified non-issues from the P0 adversarial review)

These were raised during P0 review and deliberately **not** actioned — recorded so they
aren't re-litigated:
- **Perception path "scope gate falls open"** — false positive; `_queue_perception_plan`
  creates a tool-less executor (routes to `_minimal_claude_action`), so no write occurs.
- **Resume path skips empty-capability guard** — guards a state the SVC-P0-1 fix prevents
  from being created (empty-capability steps fail before reaching `running`).
- **`_sanitize_for_span` shared-reference deepcopy** — JSON tool results have no mutable
  leaves; `audit_post_tool_hook` is read-only. Latent only.
- **`_minimal_claude_action` no enforcement** — offers no tools, so no write possible;
  observability gap, not a security hole (optional `log.warning` follow-up).
