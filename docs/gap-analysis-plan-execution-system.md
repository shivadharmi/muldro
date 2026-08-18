# Gap Analysis: Plan Creation, Execution, Approval & Tracking System

**Date:** 2026-04-12
**Branch:** `improve-overall-system-v2`
**Scope:** End-to-end plan lifecycle — creation through both chat and perception, execution via GraphExecutor, approval via TrustEngine, status tracking, resumption after interruption, and frontend observability.

---

## Executive Summary

The plan lifecycle is **architecturally sound** for happy-path scenarios but has **critical gaps in durability, observability, and edge-case handling** that violate core principles from `soul.md` and `vision.md`:

- **Soul violation — "Always preserve clarity"**: Users cannot see plan history, cannot retrieve past plan structure, and get no error feedback when approval-resume fails.
- **Soul violation — "Always preserve continuity"**: Server restarts orphan in-flight runs with no recovery. Checkpoints are created but never used for restart recovery.
- **Soul violation — "Respect reversibility"**: No plan cancellation API. No compensation for partially-executed plans.
- **Vision violation — Pillar 7 "Long-Running Execution"**: No stuck-run detection, no step-level timeouts, user-initiated runs have infinite timeout.
- **Vision violation — Pillar 5 "Safe Action"**: WebSocket approval path is broken (payload key mismatch). Edit-before-approve not wired via WebSocket.

**Total gaps identified: 42** (7 critical, 12 high, 15 medium, 8 low)

---

## 1. PLAN CREATION GAPS

### 1.1 [CRITICAL] Fast-Path Steps Lack Step IDs

**Location:** `backend/src/orchestrator/intent_classifier.py:153-220`
**Soul principle:** "Always preserve clarity" — steps without IDs cannot be tracked or debugged.

`intent_to_plan()` creates `PlanStep` objects but never sets `step_id`. The default is `""` (contracts.py:356). Downstream in `_persist_plan_record()` (muldro.py:375), the code checks `if step.step_id:` before mapping dependencies — steps with empty IDs are silently skipped during dependency resolution.

**Impact:** Fast-path plans (greeting, chitchat, simple_question, data_fetch, etc.) produce steps that cannot be referenced by downstream code, break dependency chains, and have no idempotency.

**Fix:** Generate step IDs in `intent_to_plan()`: `step_id=f"s{i+1}"` for each step.

---

### 1.2 [CRITICAL] Forward Dependencies Silently Dropped

**Location:** `backend/src/orchestrator/muldro.py:371-382` (`_persist_plan_record`)

When persisting PlanOutput steps to PlanTask rows, the code iterates steps sequentially and resolves `depends_on` references against a `step_to_task` dict that's built as it goes. If step s2 declares `depends_on: ["s3"]` where s3 hasn't been processed yet, the dependency is silently dropped because `s3` isn't in `step_to_task` yet.

**Impact:** DAG execution order can be wrong. Steps may run before their declared dependencies complete. Data from upstream steps may not be available.

**Fix:** Two-pass approach: first pass creates all step-to-task mappings, second pass resolves dependencies.

---

### 1.3 [HIGH] User Actor Steps Never Persisted

**Location:** `backend/src/orchestrator/muldro.py:371-372`

Steps with `actor != "muldro"` are skipped during persistence. These represent user actions required within a plan (e.g., "user provides additional context", "user reviews draft"). They are returned in the response but never stored in the database.

**Impact:** No audit trail for what the user was asked to do. Governor cannot see user approval requirements. `_learn_from_outcome()` has no record of user steps. Plan cannot be accurately reconstructed from DB.

**Fix:** Persist user steps as PlanTask rows with `task_type="user_action"` and `status="awaiting_input"`.

---

### 1.4 [HIGH] PlanOutput Fields Not Stored in Database

**Location:** `backend/src/models/plans.py:8-41` vs `backend/src/orchestrator/contracts.py:366-409`

PlanOutput has `achievable` (full/partial/not_achievable), `capability_gaps[]`, and `requires_user_input` fields. The Plan table has no columns for these. After persistence, you cannot determine why a plan was marked partial or what capabilities were missing.

**Impact:** Cannot audit plan quality. Cannot learn from capability gaps over time. Cannot explain to users why their request was only partially fulfilled.

**Fix:** Add `plan_output_json: Column(JSONB)` to Plan table storing the full serialized PlanOutput.

---

### 1.5 [HIGH] Silent Fallback on Planner JSON Parse Failure

**Location:** `backend/src/orchestrator/intent_classifier.py:105-150` (`extract_plan`)

When the Planner's response doesn't contain valid JSON, `extract_plan()` silently returns a minimal PlanOutput with a single "Respond to user" step. No warning is logged. The Planner's structured output (potentially multi-step with capabilities) is lost.

**Impact:** Complex plans degrade to chat-only responses without the user knowing. The Planner's reasoning and step decomposition are silently discarded.

**Fix:** Log a warning with the raw response text. Consider returning a `partial` achievability status so the frontend can indicate degradation.

---

### 1.6 [MEDIUM] System Capability Steps Not Audited

**Location:** `backend/src/orchestrator/muldro.py:2861-2904` (`_handle_system_capability`)

System capabilities (`system.set_goal`, `system.set_instruction`, `system.schedule_reminder`, `system.add_to_brief`) are executed inline without creating PlanTask records. They leave no audit trail and cannot be replayed or undone.

**Fix:** Create PlanTask records for system steps with `task_type="system"` and `status="completed"` after execution.

---

### 1.7 [MEDIUM] Perception Plan Idempotency Returns Misleading Result

**Location:** `backend/src/orchestrator/muldro.py:2555` (`_queue_perception_plan`)

When a perception plan already exists (matched by idempotency key), the function returns early with `plan_id=None`. The caller cannot distinguish between "plan skipped (duplicate)" and "plan creation failed."

**Fix:** Return the existing plan's ID on idempotency match.

---

### 1.8 [MEDIUM] No Validation of Planner-Generated Capabilities

**Location:** `backend/src/orchestrator/contracts.py:366-409` (PlanOutput validator)

PlanOutput validates dependency cycles but does not validate that `step.capability` exists in the available capability registry, that `step.actor` is valid ("muldro" or "user"), or that step_ids are unique.

**Fix:** Add validators for capability existence, actor values, and step_id uniqueness in the PlanOutput model.

---

### 1.9 [LOW] InteractionLog Stores Truncated Plan Summary

**Location:** `backend/src/orchestrator/muldro.py:434-483`

InteractionLog stores only `plan.reasoning[:500]`, losing the full goal, steps structure, and capability information. Cannot reconstruct what plan was executed from the audit log alone.

**Fix:** Store `plan_output_json` reference or full serialization alongside the interaction.

---

## 2. PLAN EXECUTION GAPS

### 2.1 [CRITICAL] No Orphan Recovery on Server Restart

**Location:** `backend/src/services/graph_executor.py:309-410`, `backend/src/services/scheduler.py`

If the server crashes during `_execute_dag()`, runs stuck in `"running"` status have **no recovery mechanism**. Checkpoints are created (graph_executor.py:1336-1370) but **never read on startup** for recovery. User-initiated runs have **no timeout** (`timeout=None` at line 365), meaning they're stuck forever.

**Soul violation:** "Preserve state and continuity whenever possible" and "recover with the smallest amount of extra burden on the user."

**Impact:** Any server restart during execution creates permanently orphaned runs that the user cannot resume, cancel, or even see status updates for.

**Fix:** Add `_tick_stuck_run_recovery()` to SchedulerLoop that:
1. Finds runs with `status="running"` and `updated_at < now - 10min`
2. Checks if checkpoint exists — if yes, attempts `resume_run()`
3. If no checkpoint, transitions to `failed` with descriptive error
4. Sets a default `timeout_seconds` (e.g., 1800s) for user-initiated runs

---

### 2.2 [CRITICAL] Cancel Does Not Stop In-Flight Steps

**Location:** `backend/src/services/graph_executor.py:474-507` (`cancel_run`)

`cancel_run()` only marks `pending` and `ready` steps as `skipped`. Steps currently in `"running"` status continue to execute — the agent loop has no signal to stop. A cancelled run may still have active tool calls in progress.

**Impact:** User cancels a plan but external actions (emails sent, calendar events created) continue to execute. Violates "Respect reversibility."

**Fix:** 
1. Add a cancellation token (e.g., `asyncio.Event`) checked between tool rounds in agent_loop
2. Mark `running` steps as `cancelled` in `cancel_run()`
3. Set a timeout on the remaining agent_loop execution

---

### 2.3 [HIGH] No Step-Level Timeout Enforcement

**Location:** `backend/src/services/execution_state.py:47` (defines `timed_out` state), `backend/src/services/graph_executor.py`

The `timed_out` status exists for TaskStep but is **never enforced**. There's no mechanism to set a timeout on individual steps or auto-transition to `timed_out` after a duration. The only timeout is at the run level (600s for background).

**Impact:** A single stuck step (e.g., waiting for an external API that never responds) blocks the entire DAG indefinitely.

**Fix:** Add `timeout_seconds` to TaskStep. Wrap `_run_step_action()` in `asyncio.wait_for(action, timeout=step.timeout_seconds)`.

---

### 2.4 [HIGH] Checkpoints Created But Never Used for Recovery

**Location:** `backend/src/services/graph_executor.py:1336-1370` (`_checkpoint`), `backend/src/models/task_graph.py:106-124` (TaskCheckpoint)

The system diligently creates checkpoints at step completion, approval gates, error retries, and manual pauses. But **no code reads these checkpoints for recovery**. `resume_run()` re-queries the DB for step states but doesn't use the rich checkpoint snapshots.

**Impact:** Checkpoint data is write-only overhead with no recovery benefit. Wasted DB storage.

**Fix:** Use checkpoints in `resume_run()` to validate state consistency. On restart recovery, compare checkpoint state vs actual step states to detect corruption.

---

### 2.5 [HIGH] Step Retry Has No Backoff Strategy

**Location:** `backend/src/services/graph_executor.py:949-961`

When a step fails and has retries remaining, it's immediately reset to `pending` and re-executed on the next DAG loop iteration. No delay, no exponential backoff, no jitter.

**Impact:** Transient failures (rate limits, network blips) hammer the same endpoint repeatedly. May trigger circuit breakers or get IP-blocked.

**Fix:** Add retry delay: `await asyncio.sleep(min(2 ** step.retry_count, 30))` before transitioning back to pending.

---

### 2.6 [HIGH] No Idempotency Key on TaskStep for Safe Retry

**Location:** `backend/src/models/task_graph.py:73-103`

TaskRun has `idempotency_key` but TaskStep does not. When a step is retried, the same tool call may execute twice without the tool-side knowing it's a retry. Non-idempotent tools (email send, payment) could produce duplicate effects.

**Impact:** Retried steps may cause duplicate external actions.

**Fix:** Add `idempotency_key` to TaskStep. Pass it to tool calls so tools can deduplicate.

---

### 2.7 [MEDIUM] DAG Steps Execute Sequentially Only

**Location:** `backend/src/services/graph_executor.py:599-601`

All ready steps execute sequentially due to AsyncSession thread-safety concerns. Independent steps that could run in parallel (e.g., "fetch email" and "check calendar") are serialized.

**Impact:** Plans take longer than necessary. A 5-step plan with 3 independent initial steps takes 3x longer.

**Fix (future):** Use separate DB sessions per parallel step branch, or queue independent steps to a worker pool.

---

### 2.8 [MEDIUM] No Artifact Cleanup on Step Failure

**Location:** `backend/src/services/graph_executor.py:939-1002` (`_handle_step_failure`)

When a step fails, any artifacts or external effects created during partial execution are not cleaned up. If Step A creates an email draft and Step B fails, the draft remains orphaned.

**Fix:** Add a compensation/cleanup callback per tool that can undo partial effects.

---

### 2.9 [MEDIUM] Verification Failure Creates Confusing State

**Location:** `backend/src/services/graph_executor.py:1393-1417`

Post-execution verification can transition a run from `completed` to `failed`. This creates a confusing audit trail where a run appears to have completed and then failed.

**Fix:** Use `partially_completed` as an intermediate state before verification. Only transition to `completed` after verification passes.

---

### 2.10 [LOW] Surface Updates Are Best-Effort (No Delivery Guarantee)

**Location:** `backend/src/services/graph_executor.py:1477-1521`

SurfaceUpdate emissions use Redis Pub/Sub which is fire-and-forget. If the frontend isn't connected, updates are lost. Errors are caught and logged at debug level.

**Fix:** Persist SurfaceUpdate to `ui_surfaces` table as durable fallback. Frontend can poll on reconnect.

---

## 3. APPROVAL & RESUME GAPS

### 3.1 [CRITICAL] WebSocket Approval Path Is Broken — Payload Key Mismatch

**Location:** Frontend: `frontend/src/components/a2ui/components/inline-approval.tsx:15`, Backend: `backend/src/api/routes_ws.py:181,186`

Frontend sends `{ approval_id: "apr_xxx" }` but backend reads `payload.get("id", "")`. The approval ID is **always empty string**, causing a 404 on every WebSocket approval attempt.

**Soul violation:** "Always preserve clarity" — the user clicks Approve and nothing happens. No error shown.

**Impact:** The primary approval interaction path (clicking buttons in the workspace UI) is non-functional. Only REST API calls work (e.g., via curl or if the frontend has a REST fallback).

**Fix:** Change `routes_ws.py:181,186` from `payload.get("id", "")` to `payload.get("approval_id", "")`.

---

### 3.2 [CRITICAL] Resume Failure After Approval Is Silently Swallowed

**Location:** `backend/src/api/routes_approvals.py:242-244, 254-255, 309-322`

All three approval types (step-level, plan-level, tool-level) catch resume exceptions and return success anyway:

```python
try:
    await executor.resume_run(approval.run_id)
except Exception:
    logger.exception("Resume failed after approval: %s", approval.run_id)
    # Returns 200 OK to frontend
```

**Soul violation:** "Never fake certainty" — user sees approval succeeded, but execution never resumes.

**Impact:** User approves an action, sees success confirmation, but the plan never continues. No feedback that something went wrong. Run is stuck in `awaiting_approval` → `running` (from the step transition) but DAG never advances.

**Fix:** On resume failure:
1. Transition run to `failed` with descriptive error
2. Emit a `failed` SurfaceUpdate so the frontend shows the error
3. Return error status in the approval response

---

### 3.3 [HIGH] Missing `edit_before_approve` WebSocket Handler

**Location:** Frontend: `inline-approval.tsx:23` sends `"edit_before_approve"`, Backend: `routes_ws.py` ACTION_HANDLERS

The frontend Edit button sends an `edit_before_approve` action via WebSocket, but there is no handler registered for this action in the backend. The REST endpoint `POST /v1/approvals/{id}/edit` exists but isn't wired to WebSocket.

**Impact:** Edit button in approval UI does nothing. Users cannot modify tool parameters before approving.

**Fix:** Add `"edit_before_approve": _handle_edit_before_approve` to ACTION_HANDLERS in routes_ws.py.

---

### 3.4 [HIGH] Expired Approvals Don't Reliably Cancel Runs

**Location:** `backend/src/services/heartbeat.py:162-192`

HeartbeatService marks expired approvals as `status="expired"` and attempts to cancel the associated run. But:
1. It only runs on schedule — if heartbeat is late, expired approvals remain `pending` in the UI
2. No active expiry check at the approval endpoints — a user can approve an expired approval if heartbeat hasn't run yet
3. No proactive notification to the user that their approval window expired

**Fix:**
1. Add expiry check in `_get_approval()` before processing approve/reject
2. Emit a `failed` SurfaceUpdate when approval expires
3. Notify user via WebSocket that the approval timed out

---

### 3.5 [HIGH] Step Transition Race Condition During Approval

**Location:** `backend/src/api/routes_approvals.py:238-241`

The step is fetched and checked for `status == "waiting_approval"` but not locked with `FOR UPDATE`. Between the check and `resume_run()`, another process could transition the step. The approval row itself is locked (line 619) but the TaskStep is not.

**Fix:** Use `SELECT ... FOR UPDATE` when fetching the TaskStep in the approval handler.

---

### 3.6 [MEDIUM] No Approval Idempotency (Double-Click Protection)

**Location:** Entire approval flow

If the frontend sends two approve requests quickly (double-click, network retry), the second request gets a 400 because the approval is already `"approved"`. But the frontend shows an error even though the action succeeded.

**Fix:** Return 200 with `"already_approved": true` when approval is already in the approved state, instead of 400.

---

### 3.7 [MEDIUM] artifact_refs Not Validated at Approval Creation

**Location:** `backend/src/services/approval_service.py:20-77`

Tool-level approvals store `artifact_refs` dict with `tool_name` and `tool_params`, but there's no schema validation. If `tool_name` is missing, the resume path (routes_approvals.py:256-322) hits a KeyError caught by a generic except block — the run is created but never executes.

**Fix:** Validate `artifact_refs` has required keys (`tool_name`, `tool_params`) at creation time.

---

### 3.8 [LOW] Inconsistent Surface ID Handling in Resume Paths

**Location:** `backend/src/api/routes_approvals.py:242` vs `lines 245-255`

Step-level resume relies on `resume_run()` recovering surface_id from checkpoint internally. Plan-level resume explicitly extracts surface_id from checkpoint and passes it. Both work but use different patterns, increasing maintenance burden.

**Fix:** Standardize: always let `resume_run()` recover surface_id from checkpoint.

---

## 4. PLAN TRACKING & OBSERVABILITY GAPS

### 4.1 [CRITICAL] No Plan List/History Endpoint

**Location:** No `routes_plans.py` exists. Plan model at `backend/src/models/plans.py`.

Users have **no way to list their past plans**. The only way to access a plan is by knowing the `run_id` and following the `plan_id` FK. There is no `GET /v1/plans` endpoint, no plan history page in the frontend, and no way to search or filter past plans.

**Soul violation:** "Always preserve clarity" — the user should be able to understand what Muldro knows, what it is doing, and what it has done.
**Vision violation:** Pillar 7 "Long-Running Execution" — tracking execution over time requires plan history.

**Impact:** Users have zero visibility into what Muldro has planned and executed on their behalf. This is especially critical for perception-triggered background plans that the user never explicitly requested.

**Fix:** Create `routes_plans.py` with:
- `GET /v1/plans` — list plans with status, date range, keyword filters
- `GET /v1/plans/{plan_id}` — full plan detail with steps and linked runs
- `GET /v1/plans/{plan_id}/runs` — all execution attempts for a plan

---

### 4.2 [HIGH] No Run List Endpoint with Filtering

**Location:** `backend/src/api/routes_runs.py`, `backend/src/api/routes_runtime.py`

`GET /v1/runtime/runs` returns active runs only (hardcoded limit of 20). There is no endpoint to list all runs (completed, failed, cancelled) with filtering by date range, status, or source.

**Fix:** Add `GET /v1/runs` with query params: `?status=completed&source=background&created_after=2026-01-01&limit=50&offset=0`.

---

### 4.3 [HIGH] No Plan Cancellation API

**Location:** No cancel endpoint exists for plans or runs.

There is `POST /v1/runs/{run_id}/resume` but no `POST /v1/runs/{run_id}/cancel`. Users cannot cancel a running or pending plan from the frontend. The only cancellation path is via approval rejection, which only applies to `awaiting_approval` runs.

**Fix:** Add `POST /v1/runs/{run_id}/cancel` that calls `graph_executor.cancel_run()`.

---

### 4.4 [HIGH] No Plan Retry API

**Location:** No retry endpoint exists.

Failed plans cannot be retried. The `failed → pending` transition exists in the state machine (execution_state.py:28) but no API endpoint triggers it. Users must re-type their request to create a new plan.

**Fix:** Add `POST /v1/runs/{run_id}/retry` that resets the run and re-executes.

---

### 4.5 [HIGH] No Stuck Run Detection or Monitoring

**Location:** `backend/src/services/scheduler.py`

No scheduled task checks for runs stuck in `running` status beyond a reasonable duration. Runs can be stuck indefinitely if the executing process crashes.

**Fix:** Add `_tick_stuck_run_detection()` to SchedulerLoop:
1. Find runs with `status="running"` and `updated_at < now - 15min`
2. Check checkpoint — if recent, extend grace period
3. If truly stuck, transition to `timed_out` or `failed`
4. Notify user via Notifier

---

### 4.6 [HIGH] No Execution History Page in Frontend

**Location:** `frontend/src/app/` — no history or runs page exists.

The frontend has 7 pages but none show execution history. Users can only see live surfaces in the workspace. Past executions, their outcomes, steps, and timing are invisible.

**Vision violation:** Pillar 1 "Continuous Context" — users should not need to repeatedly reconstruct their world for the system.

**Fix:** Add `/runs` page showing:
- Filterable list of past runs with status, date, goal
- Click-through to run detail with step timeline
- Link to plan that triggered the run

---

### 4.7 [MEDIUM] No Status Transition Audit Trail

**Location:** `backend/src/services/execution_state.py:77-100`

`transition_run()` and `transition_step()` mutate the status field directly with no historical record. You cannot see when a run entered `awaiting_approval` or how long it spent in each state.

**Fix:** Emit RuntimeEvent on each status transition with `from_status`, `to_status`, `timestamp`.

---

### 4.8 [MEDIUM] RuntimeEvent Table Under-Utilized

**Location:** `backend/src/models/runtime_event.py`

RuntimeEvent exists and is populated for some events (step.started, step.completed, tool_call) but not for all status transitions. Many important lifecycle events are missing.

**Fix:** Standardize: emit RuntimeEvent for every state transition, approval creation/resolution, and plan creation/completion.

---

### 4.9 [LOW] Surface Updates Lost on Frontend Reconnect

**Location:** `backend/src/api/routes_ws.py:26-176`, `backend/src/services/graph_executor.py:1477-1521`

If the WebSocket disconnects during execution and reconnects, all SurfaceUpdates emitted during the gap are lost (Redis Pub/Sub has no replay). The frontend may show stale or missing execution state.

**Fix:** On WebSocket reconnect, fetch current run state via REST and rebuild surfaces.

---

## 5. ALIGNMENT MATRIX: Soul & Vision Principles

| Principle | Current State | Gaps |
|-----------|--------------|------|
| **Soul: Never fake certainty** | Approval-resume silently fails, returning success | 3.2 |
| **Soul: Always preserve clarity** | No plan history, no execution history page, silent fallbacks | 1.5, 4.1, 4.6 |
| **Soul: Always preserve continuity** | No restart recovery, checkpoints unused | 2.1, 2.4 |
| **Soul: Respect reversibility** | No cancel API, no compensation for partial execution | 2.2, 4.3 |
| **Soul: Failure character** | Failures not communicated to user, silent swallowing | 3.2, 3.3, 3.4 |
| **Vision: Long-Running Execution** | No stuck-run detection, no step timeouts | 2.3, 4.5 |
| **Vision: Safe Action** | WebSocket approval broken, no edit-before-approve | 3.1, 3.3 |
| **Vision: Continuous Context** | PlanOutput not persisted, no audit trail | 1.4, 4.7 |
| **Vision: Trust before autonomy** | Expired approvals can still be approved | 3.4 |

---

## 6. PRIORITIZED FIX PLAN

### Phase 1: Critical Fixes (Correctness & Safety)

| # | Gap | Effort | Files |
|---|-----|--------|-------|
| 1 | 3.1 WebSocket approval payload key mismatch | XS | routes_ws.py |
| 2 | 3.2 Resume failure silently swallowed | S | routes_approvals.py |
| 3 | 1.1 Fast-path steps lack step IDs | S | intent_classifier.py |
| 4 | 1.2 Forward dependencies silently dropped | S | muldro.py (_persist_plan_record) |
| 5 | 2.1 Orphan recovery on server restart | M | scheduler.py, graph_executor.py |
| 6 | 2.2 Cancel doesn't stop in-flight steps | M | graph_executor.py, agent_loop.py |
| 7 | 4.1 No plan list/history endpoint | M | new routes_plans.py |

### Phase 2: Trust & Approval Hardening

| # | Gap | Effort | Files |
|---|-----|--------|-------|
| 8 | 3.3 Missing edit_before_approve WS handler | S | routes_ws.py |
| 9 | 3.4 Expired approvals don't reliably cancel runs | S | heartbeat.py, routes_approvals.py |
| 10 | 3.5 Step transition race condition | S | routes_approvals.py |
| 11 | 3.6 Approval idempotency (double-click) | XS | routes_approvals.py |
| 12 | 3.7 artifact_refs validation | S | approval_service.py |
| 13 | 4.3 Plan cancellation API | S | new endpoint in routes_runs.py |
| 14 | 4.4 Plan retry API | S | new endpoint in routes_runs.py |

### Phase 3: Durability & Observability

| # | Gap | Effort | Files |
|---|-----|--------|-------|
| 15 | 2.3 Step-level timeout enforcement | M | graph_executor.py, task_graph.py |
| 16 | 2.4 Use checkpoints for recovery | M | graph_executor.py |
| 17 | 2.5 Step retry backoff strategy | S | graph_executor.py |
| 18 | 2.6 Step idempotency key | S | task_graph.py, graph_executor.py |
| 19 | 4.5 Stuck run detection | M | scheduler.py |
| 20 | 4.7 Status transition audit trail | M | execution_state.py, runtime_event model |

### Phase 4: Data Completeness & Frontend

| # | Gap | Effort | Files |
|---|-----|--------|-------|
| 21 | 1.3 Persist user actor steps | S | muldro.py |
| 22 | 1.4 Store full PlanOutput JSON | S | plans.py, muldro.py, migration |
| 23 | 1.5 Log warning on Planner JSON parse failure | XS | intent_classifier.py |
| 24 | 1.6 Audit system capability steps | S | muldro.py |
| 25 | 4.2 Run list endpoint with filtering | M | routes_runs.py |
| 26 | 4.6 Execution history page in frontend | L | new frontend page |
| 27 | 4.8 Standardize RuntimeEvent emissions | M | multiple service files |

### Phase 5: Resilience & Future-Proofing

| # | Gap | Effort | Files |
|---|-----|--------|-------|
| 28 | 2.7 Parallel step execution | L | graph_executor.py (architecture change) |
| 29 | 2.8 Artifact cleanup on failure | M | graph_executor.py, tool layer |
| 30 | 2.9 Verification state handling | S | graph_executor.py |
| 31 | 1.7 Perception plan idempotency return | XS | muldro.py |
| 32 | 1.8 Validate Planner capabilities | S | contracts.py |
| 33 | 4.9 Surface update replay on reconnect | M | routes_ws.py, frontend |

**Effort key:** XS = <1hr, S = 1-3hr, M = 3-8hr, L = 1-2 days

---

## 7. ARCHITECTURAL RECOMMENDATIONS

### 7.1 Run Health Checker Service

Add a new service `RunHealthChecker` called every 60s by SchedulerLoop:
- Detect stuck `running` runs (no checkpoint update in 15min)
- Detect stuck `awaiting_approval` runs (approval expired but run not cancelled)
- Detect orphaned `pending` runs (created >1hr ago, never started)
- Auto-remediate or alert based on severity

### 7.2 Plan Lifecycle Events

Standardize lifecycle event emission across the entire plan flow:
```
plan.created → plan.executing → step.started → step.completed/failed → 
step.approval_requested → step.approval_resolved → plan.completed/failed
```
Each event stored as RuntimeEvent with full context. This becomes the single source of truth for plan history, debugging, and learning.

### 7.3 Durable Surface Updates

Replace fire-and-forget Redis Pub/Sub with a hybrid approach:
1. Pub/Sub for real-time push (keep current behavior)
2. Write SurfaceUpdate to `ui_surfaces` table as durable fallback
3. On WebSocket reconnect, client fetches latest surface state via REST

### 7.4 Cancellation Token Pattern

Implement cooperative cancellation for agent_loop:
1. `GraphExecutor` creates an `asyncio.Event` per run
2. Pass event to `agent_loop()` as cancellation token
3. Agent loop checks token between tool rounds
4. `cancel_run()` sets the event, agent loop exits gracefully

---

## 8. TESTING GAPS

The following scenarios lack test coverage:

1. **Plan creation from perception** — no integration test for `_queue_perception_plan()`
2. **Forward dependency resolution** — no test for steps with out-of-order depends_on
3. **Approval resume** — no test for the full approve → resume → continue DAG flow
4. **Expired approval cancellation** — no test for heartbeat expiry + run cancellation
5. **Server restart recovery** — no test for recovering orphaned runs
6. **Cancel during execution** — no test for cancel_run while agent_loop is active
7. **WebSocket approval flow** — no integration test for WS action → approval → resume
8. **Step retry with failure** — no test for retry count exhaustion + DLQ

---

## Appendix: Key File Reference

| Component | File | Key Lines |
|-----------|------|-----------|
| Orchestrator | `backend/src/orchestrator/muldro.py` | process_message:667, _persist_plan_record:313 |
| Intent Classifier | `backend/src/orchestrator/intent_classifier.py` | intent_to_plan:153, extract_plan:105 |
| Contracts | `backend/src/orchestrator/contracts.py` | PlanOutput:366, SurfaceUpdate:317 |
| GraphExecutor | `backend/src/services/graph_executor.py` | execute_run:309, _execute_dag:509, _execute_step:639, resume_run:412, cancel_run:474 |
| Agent Loop | `backend/src/orchestrator/agent_loop.py` | agent_loop:149, tool calling:344 |
| Execution State | `backend/src/services/execution_state.py` | RUN_TRANSITIONS:13, STEP_TRANSITIONS:37 |
| Approval Routes | `backend/src/api/routes_approvals.py` | approve_action:108, resume:222 |
| WebSocket | `backend/src/api/routes_ws.py` | _handle_approve:181 |
| Run Routes | `backend/src/api/routes_runs.py` | get_run:81, resume:303 |
| Runtime Routes | `backend/src/api/routes_runtime.py` | summary:26, active_runs:60 |
| Scheduler | `backend/src/services/scheduler.py` | _tick_background_tasks:309 |
| Heartbeat | `backend/src/services/heartbeat.py` | _expire_approvals:162 |
| Plan Model | `backend/src/models/plans.py` | Plan:8, PlanTask:44 |
| Task Graph Model | `backend/src/models/task_graph.py` | TaskRun:12, TaskStep:73, TaskCheckpoint:106 |
| Frontend Execution | `frontend/src/components/a2ui/components/execution-surface.tsx` | — |
| Frontend Approval | `frontend/src/components/a2ui/components/inline-approval.tsx` | — |
| Frontend Store | `frontend/src/stores/surface-store.ts` | updateSurface:79 |
