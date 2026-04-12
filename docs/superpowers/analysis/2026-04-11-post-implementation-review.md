# Post-Implementation Review: 15-Spec Execution Pipeline Redesign

**Date:** 2026-04-11
**Branch:** `improve-overall-system-v2`
**Scope:** ~200 files across orchestrator, trust, execution, surfaces, perception, data layer, frontend

---

## Executive Summary

| Severity | Count | Breakdown |
|----------|-------|-----------|
| CRITICAL | 12 | Security: 2, Runtime crash: 4, Data corruption: 2, Multi-tenant: 2, Silent failure: 1, Test failure: 1 |
| HIGH | 39 | Incorrect behavior: 15, Data integrity: 8, Missing features: 5, Race conditions: 5, Misleading tests: 6 |
| MEDIUM | 43 | Edge cases: 14, Missing validation: 10, Dead code: 11, Code quality: 8 |
| LOW | 22 | Style: 8, Naming: 7, Dead types: 4, Minor: 3 |

**Overall health:** The architecture is sound — capability-based routing, TrustEngine, surface_update events, and relevance assessment are well-designed. However, the implementation has **critical integration gaps**: the trust system has 3 disconnected paths (Governor, TrustEngine, approval-resume), the data layer has 2 Cypher injection vulnerabilities, and several multi-tenant isolation violations exist. The system will run but will produce incorrect trust decisions, leak data across workspaces in edge cases, and silently fail on several new code paths.

**Top 5 must-fix-first issues:**
1. Cypher injection in `graph_engine.py` (security — exploitable)
2. Persona batch cross-workspace leak in `scheduler.py` (multi-tenant violation)
3. `Governor._check_trust()` calls non-existent method (trust graduation permanently broken)
4. Approval-resume executor missing TrustEngine (bypasses new trust system)
5. `SurfacePreview.status="proposal"` ValidationError (proactive insights never pushed)

---

## Critical Bugs

### CRIT-1: Cypher injection via dynamic relationship label
- **File:** `backend/src/services/graph_engine.py:109-118`
- **Description:** `sync_relationship` builds Cypher relationship type via f-string interpolation of `relation_type`. No allow-list check inside the method — only `world_model.py:188` gates values, but direct callers bypass it. `relation_type.upper().replace(" ", "_")` does not sanitize injection payloads like `WORKS_ON} DELETE (n)//`.
- **Fix:** Add `if relation_type not in RELATION_TYPES: raise ValueError(...)` at the top of `sync_relationship`. Cypher labels cannot be parameterized, so allow-list is the only defense.

### CRIT-2: Cypher injection via `relation_types` list interpolation
- **File:** `backend/src/services/graph_engine.py:144-155`
- **Description:** `traverse()` interpolates a Python list directly into Cypher: `f"AND ALL(r IN rels WHERE r.relation_type IN {relation_types})"`. A caller-supplied list like `["x"] OR 1=1 //` produces injectable Cypher.
- **Fix:** Rewrite as parameterized query: `WHERE r.relation_type IN $types` with `types=relation_types` in the params dict. Validate list elements against `RELATION_TYPES`.

### CRIT-3: `Governor._check_trust()` calls non-existent `should_auto_approve`
- **File:** `backend/src/services/governor.py:226`
- **Description:** `self._trust_engine.should_auto_approve(user_id, action_type, risk_level)` — `TrustEngine` has no such method. Masked by `try/except` returning `False`, so trust graduation at the plan level is permanently broken. All plan-level trust checks silently fall through to `approval_required`.
- **Fix:** Replace with `await self._trust_engine.evaluate(capability, risk_assessment)` using the correct API, or remove the dead `_check_trust` method and rely solely on step-level TrustEngine.

### CRIT-4: Approval-resume creates executor without TrustEngine
- **File:** `backend/src/api/routes_approvals.py:223-225, 247-249, 378-380`
- **Description:** `create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)` does not inject `trust_engine` or `redis`. Resumed steps fall into legacy `requires_approval` fallback, bypassing the new TrustEngine entirely.
- **Fix:** Pass `trust_engine` and `redis` to `create_graph_executor`, or refactor to use `ServiceContainer.graph_executor`.

### CRIT-5: Persona batch leaks across workspaces
- **File:** `backend/src/services/scheduler.py:584`
- **Description:** `_tick_persona_batch` queries `InteractionLog` without `workspace_id` filter. On multi-tenant systems, one user's interactions are batched with another user's, and the entire batch is processed under `interactions[0].user_id`.
- **Fix:** Add `.where(InteractionLog.workspace_id == workspace_id)` to the query, or group interactions by `(workspace_id, user_id)` before processing.

### CRIT-6: `SurfaceKind` literal missing `"proactive_insight"`
- **File:** `backend/src/ui/contracts.py:25-37`
- **Description:** `SurfaceKind` Literal has 11 values but omits `"proactive_insight"`, which is actively used in `jarvis.py:2065`, `surface_builder.py:411`, and `routes_ws.py:312`. Any type-checked validation against `SurfaceKind` will reject proactive insight surfaces.
- **Fix:** Add `"proactive_insight"` to the `SurfaceKind` Literal.

### CRIT-7: `SurfacePreview.status="proposal"` causes silent ValidationError
- **File:** `backend/src/orchestrator/jarvis.py:2059`
- **Description:** `_push_insight_surface` sets `status="proposal"` but `SurfacePreview.status` Literal only allows `pending, running, completed, failed, awaiting_approval, cancelled`. Pydantic raises `ValidationError`, caught by `try/except Exception` — proactive insight surfaces are **never pushed**.
- **Fix:** Add `"proposal"` to `SurfacePreview.status` Literal, or use an existing status like `"pending"`.

### CRIT-8: `_current_surface_id` race on shared GraphExecutor instance
- **File:** `backend/src/services/graph_executor.py:472, 703, 837`
- **Description:** `_execute_dag` sets `self._current_surface_id` on the instance. If two runs share the same executor (scheduler background loop), the second overwrites the first's surface_id. Surface updates route to the wrong surface.
- **Fix:** Pass `surface_id` through the call chain instead of storing on the instance, or ensure each run gets a fresh executor.

### CRIT-9: `resume_run` never passes `surface_id`
- **File:** `backend/src/services/graph_executor.py:414`
- **Description:** `await self._execute_dag(run, surface_id=None)`. After approval, the live execution surface stops receiving updates permanently. The frontend shows stale "approval_needed" until page refresh.
- **Fix:** Store `surface_id` on `TaskRun` metadata and pass it through on resume.

### CRIT-10: Frontend `A2UIRenderer` crashes on undefined `children`
- **File:** `frontend/src/components/a2ui/renderer.tsx:160`
- **Description:** `surface.children.map(...)` with no null guard. If a surface arrives from REST without `children`, `TypeError` is thrown.
- **Fix:** `(surface.children ?? []).map(...)`.

### CRIT-11: Mutable defaults on `A2UIComponent` and `A2UISurface`
- **File:** `backend/src/ui/contracts.py:82, 88-90, 108, 111`
- **Description:** `properties: dict = {}`, `children: list = []`, `actions: list = []`, `metadata: dict = {}` use bare mutable defaults. Pydantic v2 handles this safely, but these models are inconsistent with the rest of the codebase which uses `Field(default_factory=...)`, and will corrupt if ever used outside Pydantic context.
- **Fix:** Replace with `Field(default_factory=dict)` / `Field(default_factory=list)`.

---

## High Issues

### Trust & Approval System

**H-1: `_trust_engine._workspace_id` mutation creates race condition**
- `backend/src/services/graph_executor.py:601`
- Shared singleton TrustEngine has `_workspace_id` mutated per step. Concurrent runs can cross-contaminate workspace trust lookups.

**H-2: `PolicyDecision.decision` literal inconsistent with `_matrix_lookup` returns**
- `backend/src/orchestrator/contracts.py:187-193` vs `trust_engine.py:105-122`
- `Governor` stores `policy_decision="approval_required"` as `TaskRun.status`, which is not a valid run status. Run becomes permanently stuck.

**H-3: Risk cache key excludes `user_context` — cross-user cache pollution**
- `backend/src/services/risk_assessor.py:60-63`
- Cache key is `capability + step_input` only. Two users with different relationship context get the same cached risk assessment.

**H-4: `graduate_trust()` edge case — high-count states stuck at `learning`**
- `backend/src/services/risk_assessor.py:180-190`
- States with `approved_count>=25` but `rejection_rate>=10%` cannot reach `trusted`, stuck at `learning`.

**H-5: Tool-level approval resume creates run without TaskSteps**
- `backend/src/api/routes_approvals.py:253-303`
- Creates `Plan` + `TaskRun` but never calls `populate_run_steps()`. DAG loop finds no pending steps, completes immediately with zero work.

**H-6: Governor `approval_type` set to risk level string, not capability**
- `backend/src/services/governor.py:189`
- Creates garbage TrustState rows with `capability="medium"`. Plan-level approvals never contribute to trust graduation.

### Orchestrator & Routing

**H-7: `user_steps` collected but never used**
- `backend/src/orchestrator/jarvis.py:789-795, 1041-1048`
- Steps with `actor="user"` are silently discarded. Plans with `requires_user_input: True` give the user no instruction.

**H-8: Unknown capability silently falls through to Operator with no tools**
- `backend/src/services/capability_resolver.py:103-113`
- Hallucinated capabilities (e.g., `"email.summarize"`) produce silent empty responses from Operator.

**H-9: Failed steps produce silent empty strings**
- `backend/src/orchestrator/jarvis.py:806-830`
- `_call_agent` swallows `LoopError` events. Failed steps appear as `""` results, no error propagation to Presenter.

**H-10: `_execute_plan_via_graph` creates ContextBuilder without graph_engine/tri_search/reranker**
- `backend/src/orchestrator/jarvis.py:2983-2987`
- Background plan execution gets degraded context compared to interactive path.

### Execution & Surfaces

**H-11: `_handle_step_failure` emits no `surface_update` on permanent failure**
- `backend/src/services/graph_executor.py:884-929`
- Surface stays on "executing" when a step fails permanently but the run has mixed completed+failed steps.

**H-12: `execute_run` called from `_handle_create_task` without `surface_id`**
- `backend/src/orchestrator/jarvis.py:3032`
- The primary plan execution path never emits live `SurfaceUpdate` events. Defeats the purpose of Spec 3A.

**H-13: `_publish_progress` creates new Redis connection per step**
- `backend/src/services/graph_executor.py:1386-1397`
- Opens and closes a Redis connection per step event. Under load, causes pool exhaustion. `self._redis` exists but is unused here.

**H-14: Step status `"waiting_approval"` vs `"awaiting_approval"` — execution surfaces excluded**
- `backend/src/services/execution_state.py:43-48` vs `surface_builder.py:253`
- Runs with `status="awaiting_approval"` excluded from `_build_active_execution_surfaces`, only shown as approval cards.

### Perception & Proactive

**H-15: `assess_relevance` JSON parsing lacks code-fence stripping**
- `backend/src/services/relevance_assessor.py:125`
- Haiku often returns code-fenced JSON. `json.loads()` fails, caught by broad `except Exception`, silently returns default. Push-tier notifications never delivered.

**H-16: Engagement suppression deadlock — no recovery path**
- `backend/src/services/engagement_service.py:79-83`
- After 5 consecutive dismissals, `suppressed=True`. Only `"engaged"` action clears it, but suppressed signals are never shown. Logical deadlock.

**H-17: Suppression is permanent — no TTL or decay**
- `backend/src/services/engagement_service.py:76-78`
- No time-based reset. Suppression is forever once triggered.

**H-18: Rate-limit TTL can fail permanently**
- `backend/src/services/notifier.py:106-110`
- If `expire` call fails after `incr`, key has no TTL. Rate limit is permanent for that user/surface.

**H-19: `"silent"` tier never updates engagement history**
- `backend/src/orchestrator/jarvis.py:1489-1515`
- Silent signals bypass engagement tracking entirely. Engagement system has no visibility into suppressed signal volume.

### Data Layer

**H-20: `conversations` collection only written during history summarization**
- `backend/src/orchestrator/jarvis.py:2208-2236`
- Conversations under 8000 chars are never embedded. Most conversations return no Qdrant hits.

**H-21: Approval vectors never evicted from Qdrant**
- `backend/src/services/eviction_service.py:202-216`
- `_evict_approvals()` deletes from Postgres but never calls Qdrant cascade. Vectors accumulate indefinitely.

**H-22: Deleted memories remain searchable for up to 7 days**
- `backend/src/api/routes_memories.py:251-252`
- `DELETE /v1/memories/{id}` sets `status="expired"` but skips Qdrant delete. Privacy issue.

**H-23: Heartbeat TTL expiry skips Qdrant delete**
- `backend/src/services/heartbeat.py:97-103`
- Same pattern as H-22. Stale vectors remain searchable until EvictionService runs.

**H-24: ContextBuilder double-writes `pack.entities` — TriSearch results discarded**
- `backend/src/services/context_builder.py:122-158`
- World-model fallback overwrites TriSearch entity results unconditionally. TriSearch entity path is a no-op.

**H-25: TriSearch excludes `conversations` and `approvals` collections**
- `backend/src/services/tri_search.py:282-284`
- Hardcoded `collections=["memories", "events", "artifacts"]`. New collections never surface in search.

### Frontend

**H-26: `"execution"` surface kind exists only on frontend**
- `frontend/src/lib/types/surfaces.ts:16`
- Backend `WorkspaceSurfacePush.kind` has no `"execution"` value. WS-pushed surfaces never match `kind === "execution"`.

**H-27: `SurfaceUpdate.phase` typed as strict `ExecutionPhase` but backend sends raw `str`**
- `frontend/src/lib/a2ui-types.ts:151` vs `backend/src/orchestrator/contracts.py:327`
- Unknown phases silently render as "Planning".

**H-28: `tool_call`/`tool_result` SSE events silently dropped**
- `frontend/src/components/jarvis/chat-panel.tsx:213`
- SSE parser has no case for tool events. UI shows no live tool activity.

**H-29: `updateSurface` overwrites steps without preserving unchanged fields**
- `frontend/src/stores/surface-store.ts:82-93`
- Partial backend updates reset `steps` to empty list, clearing existing step state.

### Models & Contracts

**H-30: No `ConfigDict(extra="ignore")` on 30+ API schemas**
- `backend/src/api/schemas.py:19-347`
- All boundary-facing models missing `extra="ignore"`. Extra fields from evolving clients raise `ValidationError`.

**H-31: `StepState.status` and `SurfaceUpdate.phase` are plain `str`, not `Literal`**
- `backend/src/orchestrator/contracts.py:288, 327`
- Agent-produced typos pass validation and break frontend rendering.

**H-32: `PolicyDecision.risk_level` is `str`, not `Literal`**
- `backend/src/orchestrator/contracts.py:196`
- Inconsistent with `RiskAssessment.risk_level` which uses `Literal["none","low","medium","high"]`.

**H-33: Trust API `VALID_TRUST_LEVELS` includes `"blocked"` which is not a graduation level**
- `backend/src/api/routes_trust.py:16`
- Setting ceiling to `"blocked"` silently corrupts trust state.

---

## Medium Issues

### Contracts & Validation (9 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-1 | `contracts.py` | 245-246 | `WorkspaceSurfacePush.preview/detail_config` typed as `Any` — circular import concern is unfounded |
| M-2 | `context_builder.py` | 61 | `ContextPack` missing `ConfigDict(extra="ignore")` |
| M-3 | `schemas.py` | 49 | `BriefingFeedbackRequest.feedback_type` should be `Literal` |
| M-4 | `schemas.py` | 50 | `BriefingFeedbackRequest.rating` has no `Field(ge=1, le=5)` |
| M-5 | `schemas.py` | 296-318 | Schedule schemas use unconstrained `str` for enum-like fields |
| M-6 | `contracts.py` | 174 | `PerceptionDecision.next_check_seconds` has no minimum — `-1` causes tight loop |
| M-7 | `routes_trust.py` | 72-76 | `TimePolicyRule.start_hour/end_hour` missing `Field(ge=0, le=23)` |
| M-8 | `relevance_assessor.py` | 126 | `RelevanceAssessment(**data)` — should use `model_validate(data)` |
| M-9 | `routes_trust.py` | 19-54 | 11 trust API models missing `ConfigDict(extra="ignore")` |

### Orchestrator & Routing (5 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-10 | `contracts.py` | PlanStep | Circular `depends_on` never validated — self-refs, cycles, forward refs silently dropped |
| M-11 | `jarvis.py` | 2500-2501 | `{capability_summary}` placeholder left verbatim when no capabilities available |
| M-12 | `jarvis.py` | 2209-2230 | `_summarize_history` references undeclared `_vector_store`, `_embedding_service` — dead code block |
| M-13 | `jarvis.py` | 1135-1143 | Race: `_spawn_background` surface push + immediate SSE `done` yield — client may not see surface |
| M-14 | `jarvis.py` | 2709-2711 | `system.respond`/`system.acknowledge` return `{}` — causes double-Presenter invocation |

### Execution & Surfaces (5 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-15 | `graph_executor.py` | 539-545 | Failed-branch `surface_update` sends no `steps` list — frontend cannot show which steps failed |
| M-16 | `surface_builder.py` | 254 | `source != "user_message"` filter undocumented — intent unclear |
| M-17 | `graph_executor.py` | 472 | `_current_surface_id` not cleaned up after `_execute_dag` returns — stale on reuse |
| M-18 | `jarvis.py` | 2059 | `_push_insight_surface` sets `detail_config=None` — frontend may render incomplete |
| M-19 | `surface_builder.py` | 360 | `rec_{i}` surface IDs not stable across requests — client loses state on refetch |

### Perception & Proactive (6 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-20 | `scheduler.py` | 636-640 | `_check_follow_ups` resets `follow_up_at` but never re-delivers — notifications sit pending forever |
| M-21 | `engagement_service.py` | 85-101 | `record_engagement` never calls `flush()` — penalty computed on stale data |
| M-22 | `relevance_assessor.py` | 104 | Model ID hardcoded — deprecation causes silent failure |
| M-23 | `relevance_assessor.py` | 68-73 | `relevance>=0.7 AND urgency="this_week"` routes to `briefing` not `push` — potentially unintentional |
| M-24 | `scheduler.py` | 583-591 | First persona batch processes all-time interactions (no time bound) |
| M-25 | `trust_engine.py` | 54 | `_graduation_progress` shows 100% AND blocked simultaneously — misleading dashboard |

### Data Layer (8 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-26 | `graph_engine.py` | 136-177 | `traverse`, `find_path`, `get_related_people` missing exception handlers |
| M-27 | `graph_engine.py` | 450-477 | `get_stale_relationships` `days` parameter is dead code |
| M-28 | `vector_store.py` | 118-125 | `ensure_indexes` bare `except: pass` suppresses real failures |
| M-29 | `vector_store.py` | 86-93 | `ensure_collections` bare `except` can silently skip creation |
| M-30 | `graph_engine.py` | 489 | `detect_communities` unbounded `[*]` pattern — OOM risk on dense graphs |
| M-31 | `memory_service.py` | 849-870 | `_composite_retrieve` Qdrant search not workspace-scoped |
| M-32 | `world_model.py` | 419-423 | Fuzzy-match entity dedup not workspace-scoped |
| M-33 | `jarvis.py + tri_search.py` | 2224-2227, 301-305 | Conversation payload missing `summary` — search text always empty |

### Frontend (5 issues)

| ID | File | Line | Issue |
|----|------|------|-------|
| M-34 | `settings/page.tsx` | 110-170 | No loading/disable on policy/budget/trust handlers — concurrent request race |
| M-35 | `use-jarvis-ws.ts` | 37 | `reconnectTimer` ref type mismatch (`undefined` vs `Timeout`) |
| M-36 | `page.tsx` | 75 | Non-deterministic sort for surfaces with null `created_at` |
| M-37 | `a2ui-types.ts` | 128 | `StepState.status` Literal on frontend stricter than backend `str` |
| M-38 | `jarvis.py` | 1096-1120 | `presenter_text` always `""` for plans with explicit respond step — surface preview blank |

---

## Low Issues

| ID | File | Issue |
|----|------|-------|
| L-1 | `contracts.py:31` | `AgentResult.response_text=""` — failed agent indistinguishable from empty response |
| L-2 | `contracts.py:46` | `StepResult.duration_ms=0` on timeout — should be `None` |
| L-3 | `schemas.py:345` | `HealthResponse` missing `ConfigDict` |
| L-4 | `ui/contracts.py:170-176` | `DetailConfig.default_tab` not cross-validated against `tabs[].id` |
| L-5 | `routes_insights.py:24` | `DismissResponse.status` should be `Literal["dismissed"]` |
| L-6 | `risk_assessor.py:147` + `trust_engine.py:30` | `_trust_level_index` duplicated in two files |
| L-7 | `trust_engine.py:270-276` | `set_ceilings_batch` is sequential N queries, not a batch |
| L-8 | `contracts.py:188` | `PolicyDecision` Literal includes `auto_execute` never produced by TrustEngine |
| L-9 | `capability_resolver.py:95` | Stale comment referencing merged agents |
| L-10 | `contracts.py:107,149` | `SpanRecord.decision` / `MessageMetadata.decision` — stale field, always empty for new plans |
| L-11 | `jarvis.py:101` | `"none"` treated as capability — undocumented |
| L-12 | `prompts.py:576-590` | `PRESENTER_PROMPT` uses old decision type strings as examples |
| L-13 | `engagement_history.py:27` | `EngagementHistory.id` uses auto-increment int, not ULID |
| L-14 | `engagement_service.py:54` | `_get_or_create` no flush after add — `IntegrityError` on concurrent calls |
| L-15 | `notifier.py:76` | `_delivered` dict grows unbounded in memory |
| L-16 | `graph_engine.py:227` | `traverse_weighted` logs failure at DEBUG — invisible in prod |
| L-17 | `types.ts:137-361` | Dead exported types: `Task`, `Goal`, `Workflow` from removed features |
| L-18 | `api.ts:497-507` | `PlanOutput` type in wrong file (`api.ts` vs `types.ts`) |
| L-19 | `jarvis.py:52-63` | `AGENT_EVENT_TYPES` includes dead `research_started`/`research_completed` |
| L-20 | `schemas/runtime.py:38` | `RuntimeEventResponse.payload: dict = {}` — should use `Field(default_factory=dict)` |

---

## Cross-Cutting Concerns

### Contract Consistency

The `PlanOutput` schema is consumed by 4 different systems with divergent expectations:

| Consumer | Reads `PlanOutput` from | Expects |
|----------|------------------------|---------|
| `jarvis.py` step loop | Planner response / intent classifier | `steps[].capability` to be routable |
| `GraphExecutor` | `TaskRun.plan` metadata | `steps[].capability` matching tool registry |
| Frontend SSE | `plan` event in stream | `steps[].description` for display |
| `surface_builder.py` | `TaskRun` linked plan | `goal` for surface title |

**Gap:** No shared validation ensures the Planner's output matches what downstream consumers need. Unknown capabilities silently fail (H-8). `depends_on` cycles are silently ignored (M-10). `actor="user"` steps are collected and discarded (H-7).

### Error Handling Patterns

The new code paths have inconsistent error handling:

| Pattern | Where | Problem |
|---------|-------|---------|
| `try/except Exception: return default` | `governor._check_trust`, `relevance_assessor.assess_relevance`, `_push_insight_surface` | Masks bugs, produces silent wrong behavior |
| `except Exception: pass` | `vector_store.ensure_indexes`, `vector_store.ensure_collections` | Suppresses connection failures alongside expected errors |
| No error handling | `graph_engine.traverse`, `graph_engine.find_path` | Exceptions propagate uncontrolled |
| Error → empty string | `_call_agent` LoopError handling | Failed steps appear successful |

**Recommendation:** Adopt a consistent pattern: catch specific exceptions, log at WARNING+, return typed error results (not empty strings/defaults).

### Async Safety

| Race Condition | File | Impact |
|----------------|------|--------|
| `_trust_engine._workspace_id` mutation | `graph_executor.py:601` | Cross-workspace trust decisions |
| `_current_surface_id` instance state | `graph_executor.py:472` | Surface updates routed to wrong surface |
| `_spawn_background` + SSE `done` | `jarvis.py:1135-1143` | Client may not see surface on immediate refresh |
| `_delivered` dict concurrent writes | `notifier.py:76` | Dict not thread-safe (asyncio is single-threaded, but re-entrant at await points) |

### Security

| Issue | File | Risk |
|-------|------|------|
| Cypher injection (2 vectors) | `graph_engine.py:113, 144` | **HIGH** — exploitable if LLM output reaches these paths unfiltered |
| Cross-workspace data leak (3 vectors) | `scheduler.py:584`, `memory_service.py:849`, `world_model.py:419` | **MEDIUM** — requires multi-tenant setup |
| Deleted memories searchable | `routes_memories.py:251` | **MEDIUM** — privacy violation for 7 days |
| Risk cache cross-user pollution | `risk_assessor.py:60` | **LOW** — same workspace only |

### Performance Concerns

| Issue | File | Impact |
|-------|------|--------|
| New Redis connection per step event | `graph_executor.py:1386` | Pool exhaustion under load |
| Unbounded `[*]` Cypher traversal | `graph_engine.py:489` | OOM on dense graphs |
| `_delivered` dict unbounded growth | `notifier.py:76` | Memory leak over long uptime |
| `ensure_indexes` bare `except: pass` | `vector_store.py:118` | Unindexed Qdrant queries (full-scan) if startup fails silently |

---

## Dead Code Sweep

### Source Code Dead References

| Pattern | File | Line | Status |
|---------|------|------|--------|
| `AGENT_EVENT_TYPES: research_started/completed` | `jarvis.py` | 52-63 | Dead — researcher merged into perceiver |
| `_execute_plan_via_graph` | `jarvis.py` | 2965-3055 | Dead in interactive path — never called from process_message |
| `_summarize_history` vector embedding block | `jarvis.py` | 2209-2230 | Dead — `_vector_store`, `_embedding_service`, `_current_user_id` never wired |
| `PRESENTER_PROMPT` old decision examples | `prompts.py` | 576-590 | Stale — references `draft_reply`, `read_source`, `research` |
| `SpanRecord.decision` field | `contracts.py` | 107 | Stale — always empty for new PlanOutput format |
| `MessageMetadata.decision` field | `contracts.py` | 149 | Stale — PlanOutput has no `decision` field |
| Frontend `Task`, `Goal`, `Workflow` types | `types.ts` | 137-361 | Dead — features removed in product redesign |
| `AGENT_MODELS` dict with `observer`/`researcher` | `budget.py` | 33-41 | Dead — dict never imported or used anywhere |
| `_DEFAULT_DISPLAY_NAMES` with `observer`/`researcher` | `agent_registry.py` | 22-41 | Dead keys — `seed_defaults()` only iterates `AGENT_PROMPTS` |
| `_DEFAULT_DESCRIPTIONS` with `observer`/`researcher` | `agent_registry.py` | 22-41 | Dead keys — `perceiver` falls back to `None` description |
| `agents.py` comments referencing observer/researcher | `agents.py` | 27, 59, 64 | Stale comments in perceiver capability scope block |

### Script Dead References (will crash on import)

| Pattern | File | Line | Status |
|---------|------|------|--------|
| `from src.integrations.capabilities import TOOL_TO_CAPABILITY` | `scripts/explore_tools.py` | 277 | Deleted symbol — `ImportError` |
| `from src.services.tool_registry import _DEFAULT_TOOLS` | `scripts/explore_tools.py` | 288 | Deleted symbol — `ImportError` |
| `CANONICAL_ALIASES` usage | `scripts/explore_tools.py` | 328-357, 511-555 | Deleted symbol — `ImportError` |

### Test Dead References (will fail or mislead)

| Pattern | File | Line | Severity |
|---------|------|------|----------|
| Expects `observer`+`researcher` agents + count `>=8` | `tests/e2e/test_03_service_chains.py` | 166-178 | **CRITICAL** — will fail on fresh DB |
| `AgentEnvelope(agent_name="observer")` | `tests/test_contracts.py` | 29, 40-45 | HIGH — misleading coverage |
| `governor_pre_tool_hook` with `agent="observer"/"researcher"` | `tests/golden/test_governor_policies.py` | 28-34 | HIGH — wrong agent identity tested |
| `SubAgent(name="researcher")` | `tests/test_unified_dispatch.py` | 288, 332 | HIGH — dispatch tests use dead name |
| `record_from_span(agent_name="observer")` | `tests/test_foundation_hardening.py` | 531 | HIGH — budget span with dead agent |
| `_assemble_context("observer"/"researcher")` | `tests/test_context_assembler.py` | 32, 147 | HIGH — context assembly with dead agents |
| `"agent_name": "observer"` in span fixtures | `tests/test_trace_store.py` | 78, 126, 142 | MEDIUM — stale fixtures |
| `"agent_name": "observer"` in alerting fixtures | `tests/test_alerting.py` | 38, 62, 124, 176 | MEDIUM — stale fixtures |
| `_resolve_pipeline` no-op attribute assignment guard | `tests/test_orchestrator_routing.py` | 112-116, 199-202 | MEDIUM — weak guard |

---

## Actionable Insights

### What Was Implemented Well

1. **Capability-based routing architecture** — Clean separation between Planner (decides capabilities), CapabilityResolver (maps to agents/tools), and agent loop (executes). This is significantly better than the old 19-decision-type dispatch.

2. **TrustEngine matrix design** — The `(trust_level × risk_level) → decision` matrix is elegant and well-structured. The `RiskAssessment` Pydantic model is one of the best-validated models in the codebase.

3. **SurfaceUpdate event protocol** — The `SurfaceUpdate` contract with `phase`, `steps[]`, `approval`, `results` is a clean real-time execution protocol. The design is good even though the wiring has gaps.

4. **Relevance assessment pipeline** — The `assess_relevance → determine_tier → route_notification` pipeline is a clean 3-stage architecture that correctly separates concerns.

5. **Engagement history as a learning signal** — Using dismissal/engagement data to suppress low-value signals is architecturally sound.

### What Was Implemented Inconsistently

1. **Trust system has 3 disconnected paths** — Governor (plan-level, broken `_check_trust`), TrustEngine (step-level, works), approval-resume (no TrustEngine at all). These need to converge into a single trust evaluation path.

2. **Surface emission is wired in some paths but not others** — `_execute_dag` emits `surface_update`, but `_handle_create_task` doesn't pass `surface_id`. `resume_run` doesn't pass it. Step failures don't emit. The system is half-wired.

3. **Qdrant collection coverage** — `events` and `memories` are well-integrated. `conversations` is barely written. `approvals` is written but never searched or evicted. Collection lifecycle is inconsistent.

4. **Error handling** — Some paths use broad `except Exception` with defaults (silent failure), others have no handling at all, and a few use targeted exception types. No consistent pattern.

5. **Workspace scoping** — Main query paths are scoped. But Qdrant search (M-31), entity dedup (M-32), and persona batch (CRIT-5) miss workspace filters.

### Technical Debt Introduced

1. **`_current_surface_id` as instance state** — Should be passed through the call chain. Instance-level mutable state on a shared executor is a recurring source of bugs.

2. **`create_graph_executor` factory doesn't inject all dependencies** — The factory creates an incomplete executor. Callers must know to use `ServiceContainer.graph_executor` instead. This should be one path.

3. **Governor + TrustEngine coexistence** — Governor's `_check_trust` calling a non-existent method suggests the two systems were meant to be integrated but weren't. The Governor should either delegate to TrustEngine or be deprecated.

4. **Hardcoded model IDs** — `claude-haiku-4-5-20251001` in relevance_assessor, risk_assessor. Should come from settings.

5. **`SurfaceKind` maintained in two places** — `ui/contracts.py` and `orchestrator/contracts.py` have divergent kind sets. Should be a single canonical Literal.

### Recommendations for Next Steps

**Immediate (before any testing):**
1. Fix Cypher injection (CRIT-1, CRIT-2) — security critical
2. Fix `SurfacePreview.status` ValidationError (CRIT-7) — blocks proactive insights entirely
3. Add workspace filter to persona batch (CRIT-5) — multi-tenant violation
4. Fix frontend `A2UIRenderer` null guard (CRIT-10) — runtime crash

**Short-term (before release):**
5. Unify trust evaluation path — remove `Governor._check_trust`, inject TrustEngine into approval-resume executors
6. Wire `surface_id` through `resume_run` and `_handle_create_task`
7. Add `"proactive_insight"` to `SurfaceKind` Literal
8. Strip code fences in `assess_relevance` JSON parsing
9. Add engagement suppression TTL (e.g., auto-clear after 7 days)
10. Include `conversations`/`approvals` in TriSearch collections

**Medium-term (next iteration):**
11. Add `Literal` constraints to `StepState.status`, `SurfaceUpdate.phase`, `PolicyDecision.risk_level`
12. Add `ConfigDict(extra="ignore")` to all API schemas
13. Validate `PlanStep.depends_on` for cycles and self-references
14. Fix ContextBuilder double-write of `pack.entities`
15. Implement Qdrant cascade delete in `_evict_approvals` and memory deletion API
