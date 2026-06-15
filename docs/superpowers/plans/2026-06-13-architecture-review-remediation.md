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
| P1 | 9 | 🟡 8 done (M1–M3), 1 planned |
| P2 | 12 | 🟡 8 done (M1–M3), 4 planned |
| P3 | 11 | 🟡 5 done (M1–M3, M5), 6 planned |

**Milestone 1 (security-adjacent hardening) — ✅ complete + reviewed.** See §2.1.
**Milestone 2 (correctness quick-wins) — ✅ complete + reviewed.** See §2.2.
**Milestone 3 (dependency direction, Theme B) — ✅ complete + reviewed.** See §2.3.
**Milestone 5 (god-object extractions, Theme C) — ✅ complete + reviewed** (targets #1–#7, all
structure-only; the full ORCH-P1-1 stream/non-stream Adapter fold is deferred as a separate
behavior-change spec). See §2.4.

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

### 2.1 Milestone 1 — security-adjacent hardening (completed on this branch)

Implemented, reviewed (no CRITICAL/HIGH findings), and committed as six logical commits.

- **SVC-P2-1** — RiskAssessor fails **closed** to `risk_level="high"` on assessment
  failure (both `risk_assessor.py` and `graph_executor._assess_step_risk`). `high` →
  `approval_required` at every trust level, so an outage can't auto-execute a write.
- **TOOL-P1-1** — `validate_registry()` errors (and harness failures) now **abort startup**
  (`api/app.py`); still bypassable via `JARVIS_SKIP_REGISTRY_VALIDATION`.
- **TOOL-P1-3** — Validation Check 4 extended from dead `critical`-only to `("high","critical")`
  write tools missing approval. Verified it passes the live catalog; `medium` (browser_*)
  intentionally excluded.
- **UI-P3-2** — `_handle_execute_insight` surface lookup **and** the WS reconnect-backfill
  query are now workspace-scoped (`routes_ws.py`). Closes a cross-tenant gap. (The
  `action_index` bounds-check already existed.)
- **ORCH-P1-3** — Documented the chat-path-ungated-by-design + `_capability_in_scope`
  compensating control in CLAUDE.md, with guardrail bullets so it isn't "fixed" by mistake.
- **TOOL-P2-2** — Retired the **dead** `oauth_connections` table (model + `complete_oauth` +
  `_upsert_oauth_connection` + Alembic drop migration). Investigation found `oauth_tokens` is
  canonical. The surviving `oauth_tokens` index is **left as-is by design**: tokens are
  user-level (every reader keys on `(user_id, provider)` with no `workspace_id`), so the
  plan's `(workspace_id, user_id, provider)` change would be inert without a multi-file
  feature; documented in `oauth_token.py` instead.

### 2.2 Milestone 2 — correctness quick-wins (completed on this branch)

Implemented, reviewed (no CRITICAL/HIGH findings), and committed as seven logical commits.

- **ORCH-P1-2** — `_api_call_with_retry` raises `RuntimeError` after the loop instead of an
  implicit `None` fall-through.
- **ORCH-P3-4** — `audit_post_tool_hook` now receives the real per-tool token share, not `0`.
- **ORCH-P2-3** — `classify_intent` parses via `parse_llm_json` (robust to prose/fences)
  instead of naive `index`/`rindex` brace matching.
- **ORCH-P3-1** — `budget.calculate_cost` warns before the Sonnet-pricing fallback for an
  unknown model (was silent ~5× under-billing for Opus/Bedrock).
- **ORCH-P3-2** — orchestrator `shutdown()` cancels + logs background-task stragglers instead
  of abandoning them silently.
- **TOOL-P1-2** — `seed_defaults` writes + re-syncs `InternalToolDef.description` (was
  permanently NULL).
- **TOOL-P2-3** — `validate_registry` Check 7 asserts global tool-name uniqueness.
- **UI-P2-1** — `surface_mapping` exception handling narrowed from `(JSONDecodeError,
  Exception)` to `(JSONDecodeError, ValidationError)` + non-dict guards (both extractors).
- **UI-P2-2** — `surface-store.updateSurface` uses `!== undefined` so an empty `steps` array
  can clear the list.
- **UI-P2-4** — `normalizeSurfaceKind` warns on unknown (drifted) surface kinds while keeping
  the `summary` visual fallback; wired into the REST + all WS/SSE ingest paths.
- **SVC-P1-1** — Removed the undocumented lenient `25+@<15%→trusted` graduation branch
  (it failed unsafe and fought demotion); reconciled to the documented 3-tier rule with
  `GRADUATION_THRESHOLDS`/`LEARNING_MIN_APPROVED` as a single source of truth shared by the
  gate and the UI progress logic. Lazy propagation — no migration. Boundary + gate/UI
  consistency tests added.

### 2.3 Milestone 3 — dependency direction / Theme B (completed on this branch)

Mechanical relocations of shared symbols to neutral homes both layers import downward.
Implemented, reviewed (no findings), committed as three logical commits; full non-e2e
suite green (2221 passed).

- **SVC-P1-4** — `MODEL_TIERS`/`BEDROCK_MODEL_TIERS` moved from `orchestrator.jarvis` to
  `src/config/models.py`; the byte-identical `_get_haiku_model()` duplicated in
  `risk_assessor` + `relevance_assessor` is now one shared `get_haiku_model()` in config.
- **SVC-P1-2** — `resolve_workspace_id` → `src/services/workspace_resolver.py` (`api.deps`
  re-exports it for route handlers); evidence models → `src/services/evidence_models.py`.
  No service/orchestrator/integration/interface module imports from `src.api` anymore.
- **SVC-P2-3** — `git mv src/orchestrator/contracts.py → src/contracts/__init__.py`; all 43
  importers rewritten `from src.orchestrator.contracts` → `from src.contracts`, no shim.

> **Out of scope (noted, not actioned):** pre-existing service→orchestrator imports of
> *behavioral* symbols (`agent_registry`→`SubAgent`, `graph_executor`→`agent_loop`/`tracing`,
> `trace_store`→`budget`). These are structural (the DAG executor wraps the agent loop), not
> shared-symbol relocations, so they're a separate architectural question.

### 2.4 Milestone 5 — god-object extractions / Theme C (in progress)

Each god object is its own change: characterization/seam tests first, structure-only move,
structure and behavior in separate commits. Done bar is pragmatic (extract the seams, shrink
meaningfully, stop growth — not force every file under the 800-line cap). Order is
easiest/lowest-risk → hardest, decided from live analysis (sizes were larger than this doc's
original numbers). All five analyses confirmed **zero circular-import risk** because M3 moved
shared contracts to the neutral `src/contracts/` package.

Order: **ORCH-P3-3 → SVC-P2-2 (surface_detail_builders → intelligence_server/TOOL-P2-4 →
memory_service → scheduler) → SVC-P1-3 (graph_executor) → ORCH-P1-1 (jarvis stream fold)**.

- **ORCH-P3-3** — ✅ done. Extracted the five `system.*` capability handlers from
  `JarvisOrchestrator` into a constructor-injected `SystemCapabilityHandler`
  (`src/orchestrator/system_capability_handler.py`); the hub delegates via a thin
  `_handle_system_capability`. Structure-only; method bodies moved verbatim (dispatcher
  renamed to public `handle_system_capability`). `jarvis.py` 3519 → 3265 lines. The existing
  characterization test (`test_system_capability_handler.py`) passed **unmodified** (zero
  behavior change); added standalone handler tests; repointed one source-location assertion
  in `test_plan_creation.py`. Reviewed (no findings); full non-e2e suite green (2223 passed).
- **SVC-P2-2a** (`surface_detail_builders.py`) — ✅ done. Split the 1610-line module into a
  package: `_shared.py` (helpers) + nine per-surface-kind submodules (plan, summary, briefing,
  approval, recommendation, alert, lists, insight, run) + an `__init__.py` **facade**
  re-exporting every builder and assembling `TAB_BUILDERS`. Import path unchanged; function
  bodies sliced verbatim (byte-identical). Largest module now 359 lines. Cross-kind
  delegations resolve via sibling imports (no cycles). Added a registry exact-snapshot
  characterization test. Reviewed (true no-op, no findings); suite green (2224 passed).
  Remaining SVC-P2-2 files (`scheduler.py`, `memory_service.py`) tracked separately below.
- **TOOL-P2-4** (`intelligence_server.py`) — ✅ done. Split the 1214-line FastMCP module into
  a package: `_shared.py` (the `intelligence` instance + runtime globals + `configure()`/
  `_get_db()`) + four domain submodules (observation, memory, planning, persona) + an
  `__init__.py` facade that imports all four (registering decorators) and re-exports the
  prior public names. Import path unchanged. Tool bodies sliced verbatim; the sole edit was
  qualifying the two runtime-rebound globals `_services`/`_settings` → `_shared._services`/
  `_shared._settings` (attribute access reads the configured value at call time). Largest
  module 410 lines. Added a registration-integrity test pinning the exact 19 tool + 2
  resource-template names; repointed three tests' internal-global patch targets to `_shared`.
  Reviewed (true no-op, no findings); suite green (2227 passed).
- **SVC-P2-2b** (`memory_service.py`) — ✅ done. Decomposed the 1142-line single-class module
  into a package of per-responsibility **base classes** that `MemoryService` inherits:
  `_base` (collaborators + shared helpers), `extraction`, `storage`, `retrieval`,
  `consolidation`, `contradictions`, `stability`; `service.py` composes them via multiple
  inheritance; `__init__` is the facade. **Inheritance (not delegation) was chosen so method
  bodies move byte-for-byte verbatim** — no `self`→`svc` rename, no forwarding stubs. This
  deliberately overrides engineering-standards §2's "avoid mixins" for this case (one-level
  composition of a single cohesive stateless class, user-directed). Largest module 284 lines.
  Added a composition characterization test (all 20 methods resolve, single `__init__`, MRO);
  moved five tests' `get_anthropic_client`/`EmbeddingService` patch targets to `_base`.
  Reviewed (true no-op, no findings); suite green (2231 passed). This completes all three
  SVC-P2-2 files except `scheduler.py` (SVC-P2-2c below).
- **SVC-P2-2c** (`scheduler.py`) — ✅ done. Decomposed the 1226-line single-class module
  (`SchedulerLoop`) into a package of per-responsibility **mixin base classes** the final
  `SchedulerLoop` inherits: `_base` (`__init__`, run/stop, the `_tick` cadence dispatcher,
  module-level `compute_next_run`, shared workspace/source helpers, and the mutable tick state
  `_tick_count`/`_last_persona_batch_at`), plus one mixin per `_tick_*` group — `perception_tick`,
  `background_tasks_tick`, `lifecycle_tick` (eviction/memory-expiration/consolidation/stability),
  `dlq_tick`, `notification_tick`, `run_health_tick`, `persona_tick`, and `schedule_dispatch`
  (`_fire`); `service.py` composes them; `__init__` is the facade (re-exports `SchedulerLoop`,
  `compute_next_run`, `get_session_factory`). **Inheritance (not delegation)** so bodies move
  byte-for-byte verbatim — same user-directed override of engineering-standards §2 as SVC-P2-2b.
  Largest module 226 lines (was 1226). The sole body change: `ruff --fix` dropped a redundant
  local `timezone` re-import in `_tick` (resolves to the module-level import; identical). Added
  `tests/test_scheduler_seam.py` characterizing the tick-cadence gating, the method surface, and
  `compute_next_run`; re-pointed moved `mock.patch` targets to the submodule where each symbol is
  looked up (`_tick`→`_base`, `_fire`→`schedule_dispatch`, DLQ→`dlq_tick`). Reviewed (true no-op,
  no findings); suite green (2239 passed = 2231 baseline + 8 seam tests). Completes SVC-P2-2.
- **SVC-P1-3** (`graph_executor.py`) — ✅ done. **First collaborator (delegation) extraction**,
  not a package/inheritance split: `GraphExecutor` stays a frozen hub. Pulled the cohesive
  Redis/event-bus emission cluster out into a new leaf module
  `src/services/execution_surface_emitter.py` (`SurfaceEmitter`): `emit_event`,
  `publish_progress`, `emit_surface_update`, `emit_summary_surface`. `__init__` builds
  `self._surface_emitter` from already-injected deps (`settings`/`db`/`event_bus`/`redis`/
  `db_factory`) so the **public constructor signature is byte-identical**; the 4 hub methods
  become **thin forwarders** (chosen over removing them so the ~25 internal call sites and the
  ~30 tests doing `executor._emit_X = AsyncMock()` stay valid — the frozen-hub collaborator-DI
  pattern). Bodies moved verbatim (AST-verified); sole change `self._publish_progress` →
  `self.publish_progress` inside `emit_event` (that method moved too). Hub 2116 → 1897 lines;
  collaborator 325. **No `patch("…graph_executor.X")` target moved** — the moved bodies use
  `select`/`TaskStep`, re-imported in the new module and patched nowhere. Added
  `tests/test_execution_surface_emitter.py` (9 characterization tests, GREEN on pre-refactor
  code); re-pointed 2 emit→progress wiring tests (`test_ws_progress.py`) + 1 `getsource`
  assertion (`test_execution_durability.py`) to the collaborator, and switched
  `test_graph_executor_surface_updates.py` to pass transport deps via the constructor (the
  collaborator snapshots them in `__init__`; post-construction reassignment would not reach it —
  confirmed no prod code does this). **SVC-P3-1 deferred**: the `elif not self._trust_engine:`
  branch is reachable (`create_graph_executor` leaves `trust_engine=None` if `TrustEngine`
  construction raises), so removing it is a separate behavior change, not part of this structural
  move. Reviewed (true no-op, no findings); suite green (2248 passed = 2239 baseline + 9 seam
  tests). Remaining M5 target: ORCH-P1-1 (`jarvis.py` stream/non-stream fold).
- **ORCH-P1-1** (`jarvis.py`) — ✅ done (**safe-extraction scope**, by decision). Live analysis
  found the literal "fold `process_message`/`process_message_stream` into one core + thin adapter"
  to be **behavior-changing**, not structural: the two methods share the
  intent→plan→route→execute→present *sequence* but have **drifted** — different presenter prompt
  text, different agent-context source (non-stream injects from the `result` dict, stream from
  `step_outputs`), different events (`plan_generated`+`run_completed` via await vs
  `plan_created`+`plan` SSE), and fundamentally different output contracts (batch `result` dict —
  returned verbatim to WS clients by `routes_ws` — vs SSE event stream) consumed by 7 callers vs 1.
  Folding would alter LLM prompts and the WS API on the primary chat path, so it cannot be a
  structure-only commit. Scope narrowed to extracting only the **byte-identical** shared blocks
  into new module `src/orchestrator/chat_pipeline.py` as **stateless free functions** (NOT a
  collaborator class — engineering-standards §2 "functions over a one-method class"):
  `resolve_plan_routing`, `build_telegram_hint`, `build_user_action_block`,
  `format_prior_step_results`, `format_prior_results_for_presenter`. Both methods' five inline
  blocks each (routing pre-resolution, agent prior-results injection, user-action block, presenter
  prior-results block, telegram hint) now call these; bodies moved verbatim, empty-input guards
  proven equivalent (functions return `""` exactly when the dict is empty, so `+= ""` and
  `if block:` match the originals). Divergent logic (mode, prompt text, events, `direct_answer`,
  result-dict shape) left untouched. `route_step` import dropped from `jarvis.py`;
  `CapabilityResolver`/`PlanStep` retained. `jarvis.py` 3265 → 3184. Kills the documented "wire it
  into BOTH methods" drift failure mode for these blocks. Added `tests/test_chat_pipeline.py` (15
  RED-first tests pinning exact strings + routing tuples). Reviewed (behavior-identical, no
  findings); suite green (2263 passed = 2248 baseline + 15). **The full stream/non-stream Adapter
  fold is DEFERRED** as a separate behavior-change spec (drift reconciliation needs its own
  characterization + sign-off). **This completes Milestone 5** (targets #1–#7, all structure-only).

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
