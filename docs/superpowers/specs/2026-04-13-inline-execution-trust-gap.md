# Inline Execution Trust Gap — Spec B1

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Severity:** ~~CRITICAL~~ → **BY DESIGN** (resolved 2026-04-13)
**Discovered during:** E2E testing of plan-execution pipeline

---

## Resolution: No Fix Needed — User Intent IS Authorization

After analysis and design review, the observed behavior is **correct by design**:

- The **chat path is user-initiated**: when the user types "send that email," their message IS the authorization. Adding an approval gate to a user-requested action is redundant friction.
- The **TrustEngine exists for autonomous actions**: perception-triggered plans, scheduled tasks, and background execution — where Jarvis acts without explicit user consent. These paths already go through GraphExecutor with full trust gates.
- The **`mode="plan"` option** already handles the "review before executing" case: it skips risky steps and presents the plan for user approval before execution.

**Design principle:** Chat path = user authorized. Background path = needs trust gates. Plan mode = user wants to review first.

Two implementation approaches were evaluated and rejected:
- **Approach A (inline trust gate):** Implemented, tested (8 tests passing), then reverted. Added TrustEngine.evaluate_plan_risk() before _call_agent_stream() for operator steps. Rejected because it gates user-initiated actions unnecessarily.
- **Approach B (route write plans through GraphExecutor):** Designed but not implemented. Would create TaskRun and spawn background execution for write plans. Rejected because GraphExecutor is designed for autonomous execution, not user-initiated chat.

---

## Original Analysis (preserved for reference)

### Problem Statement (reclassified as expected behavior)

Write capabilities executed via the chat path (`process_message_stream`) **bypass the TrustEngine entirely**. The user's `policy_mode=approval_required` and the plan's `execution_mode=approval_required` are both ignored. External actions (email send, calendar create, etc.) execute without approval.

### Evidence from E2E Testing

1. User sent: "Send the draft email reply to Vertex Horizon Ventures investor"
2. Planner created a 4-step plan with `risk_level=high`, `execution_mode=approval_required`
3. Plan persisted to DB with 4 PlanTasks including `email.send`
4. **Operator agent executed `email.send` without any approval check**
5. Email was actually sent to a real recipient
6. Trust dashboard shows no `email.send` trust state was ever created
7. No Approval record was created
8. No TaskRun/TaskStep records exist — execution was entirely inline

### Root Cause

There are **two execution paths** in the orchestrator, but only one has trust gates:

```
Path A: Background/Perception (HAS trust gates)
  _queue_perception_plan() → create_run() → GraphExecutor.execute_run()
    → _execute_step() → TrustEngine.evaluate() → approval gate
    → checkpoint, DAG scheduling, state machine

Path B: Chat/Interactive (NO trust gates)
  process_message_stream() → _call_agent_stream() per step
    → agent_loop() → tool execution
    → NO TrustEngine check, NO approval creation
    → NO TaskRun, NO checkpoint, NO state machine
```

The gap is in `process_message_stream()` at `jarvis.py:1162-1217`. The step execution loop iterates over `step_routing` and calls `_call_agent_stream()` directly for each step. There is no TrustEngine evaluation, no risk assessment, and no approval gate.

### Exact Code Location

**File:** `backend/src/orchestrator/jarvis.py`

**Lines 1159-1217** — Step 3: Execute steps with streaming:
```python
# Step 3: Execute steps with streaming
step_outputs: dict[str, str] = {}
for step, agent_name, tools in step_routing:
    if step.capability.startswith("system."):
        await self._handle_system_capability(step, plan, user_id, workspace_id)
        continue

    if not agent_name:
        # ... error handling ...
        continue

    # Plan mode: skip risky execution, present the plan
    if mode == "plan" and step.risk in ("medium", "high"):
        yield {"event": "plan_ready", ...}
        continue

    # ⚠️ NO TRUST CHECK HERE — executes directly
    async for evt in self._call_agent_stream(
        agent_name,
        message=agent_message,
        ...
    ):
        yield evt
```

**Key observation:** Line 1174 shows that `mode == "plan"` DOES skip risky steps. But `mode == "execute"` and `mode == "ask"` (default) both fall through to direct execution with no trust check.

### What Exists But Isn't Connected

The following components exist and work correctly — they just aren't invoked from the inline path:

| Component | Location | Status |
|-----------|----------|--------|
| `TrustEngine.evaluate()` | `src/services/trust_engine.py` | Works (verified in GraphExecutor) |
| `RiskAssessor.get_or_assess_risk()` | `src/services/risk_assessor.py` | Works (has Redis cache) |
| `create_approval()` | `src/services/approval_service.py` | Works |
| `GraphExecutor._execute_step()` | `src/services/graph_executor.py` | Has full approval gate |
| Governor pre-tool hook | `src/orchestrator/hooks.py` | Audit-only (checks blocked tools, doesn't gate) |

---

## Scope of Impact

### What's Affected

Any write capability executed via chat:
- `email.send` — sends real emails without approval
- `email.draft` — creates drafts (lower risk but still uncontrolled)
- `calendar.create` / `calendar.update` — modifies calendar
- `slack.send_message` — posts to Slack channels
- `github.create_issue` / `github.create_pr` — creates GitHub items
- Any other `requires_approval=True` tool

### What's NOT Affected

- Background/perception plans — these go through GraphExecutor correctly
- Scheduled tasks — same, through GraphExecutor
- Read-only capabilities — no approval needed anyway
- `mode="plan"` — correctly skips risky steps (but doesn't create approvals)

---

## Proposed Fix: Two Approaches

### Approach A: Inline Trust Gate (Recommended)

Add a TrustEngine check in the inline step execution loop, before calling `_call_agent_stream()` for write capabilities. When approval is needed, yield a `plan_ready` event and pause.

**Where:** `jarvis.py:1162-1217`, inside the `for step, agent_name, tools in step_routing:` loop.

**Logic:**
```python
for step, agent_name, tools in step_routing:
    # ... existing system capability and error checks ...

    # NEW: Trust gate for write capabilities
    if step.risk in ("medium", "high"):
        async with self._db_factory() as db:
            from src.services.risk_assessor import get_or_assess_risk
            from src.services.trust_engine import TrustEngine

            risk = await get_or_assess_risk(...)
            te = TrustEngine(db)
            decision = await te.evaluate(workspace_id, step.capability, risk.risk_level)

            if decision.decision == "approval_required":
                # Create approval, emit event, pause execution
                approval = await create_approval(...)
                yield {"event": "approval_needed", "approval": {...}}
                # Store partial results, return — user must approve to continue
                break
            elif decision.decision == "blocked":
                yield {"event": "step_blocked", "step_id": step.step_id, ...}
                continue
            # auto_execute_notify / auto_execute_silent: proceed normally

    # Existing execution code
    async for evt in self._call_agent_stream(...):
        yield evt
```

**Pros:** Minimal code change (~30 lines). Reuses existing TrustEngine/RiskAssessor. No architectural change.
**Cons:** SSE stream pauses — frontend needs to handle `approval_needed` event mid-stream. Resumption is complex (need to replay remaining steps after approval).

### Approach B: Route Write Steps Through GraphExecutor

When the plan has write capabilities, create a TaskRun and route execution through GraphExecutor instead of inline. The chat stream yields execution surface updates.

**Where:** `jarvis.py:1094-1104` (after plan persistence), redirect to GraphExecutor.

**Logic:**
```python
# After plan persistence, check if GraphExecutor path needed
has_write_steps = any(s.risk in ("medium", "high") for s in plan.steps if s.actor == "jarvis")
if has_write_steps and plan.plan_id:
    # Create TaskRun and execute via GraphExecutor (has trust gates)
    executor = await create_graph_executor(...)
    run = await executor.create_run(plan_id=plan.plan_id, ...)
    yield {"event": "execution_start", "run_id": run.run_id}
    await executor.execute_run(run.run_id, surface_id=...)
    yield {"event": "execution_result", ...}
    # Skip inline step loop entirely
```

**Pros:** Full trust infrastructure (approval gates, checkpoints, DAG, state machine). TaskRun/TaskStep records created. Resumption works automatically. Surfaces update live.
**Cons:** Larger change. SSE stream changes from agent events to execution events. Frontend needs to handle the execution surface in the chat pane. May increase latency for simple write operations.

### Recommendation: Approach A for immediate fix, Approach B as follow-up

Approach A stops the bleeding — write capabilities get a trust check immediately. Approach B is the proper architectural solution but requires frontend changes for the approval flow in the chat pane.

---

## Affected Files

| File | Change | Approach |
|------|--------|----------|
| `backend/src/orchestrator/jarvis.py` | Add trust gate in step execution loop | A |
| `backend/src/orchestrator/jarvis.py` | Route write plans to GraphExecutor | B |
| `frontend/src/app/chat/page.tsx` | Handle `approval_needed` SSE event | A |
| `frontend/src/components/a2ui/components/inline-approval.tsx` | Render approval in chat stream | A |

---

## Testing Strategy

### Verification Tests

1. **Send message with write capability in "Ask" mode** → should trigger trust check
2. **Send message with write capability in "Execute" mode** → should trigger trust check
3. **Send message with read-only capability** → should execute without trust check (no regression)
4. **Policy mode "approval_required" + first_use trust** → should create Approval and pause
5. **Policy mode "approval_required" + trusted trust** → should auto-execute with notification
6. **Approve the paused execution** → should resume and complete
7. **Verify trust state updated** → approved_count incremented, graduation progress

### Regression Tests

- Fast path intents (greeting, chitchat) still work without trust checks
- Read-only plans (email.search, knowledge.search) execute without interruption
- Background/perception plans still use GraphExecutor path (not affected)
- `mode="plan"` still skips risky steps and shows plan_ready event

---

## Dependencies

- **Spec A (execution-hardening)** should be merged first — the trust locking fix (Fix 10) ensures `record_approval_decision` is safe for concurrent writes.
- **Frontend inline approval** (Approach A) requires the `approval_needed` SSE event to be rendered. The existing `InlineApprovalCard` component could be reused in the chat pane.

---

## Context for Next Session

### What was tested and confirmed working:
- Fast intent classification (chitchat, status_query) ✅
- Full Planner decomposition (multi-step with deps) ✅
- Inline agent pipeline execution (5 agents sequentially) ✅
- Real MCP tool calls (Gmail search/read/draft/send) ✅
- Plan persistence with tasks and dependencies ✅
- SSE streaming with agent events ✅
- Frontend rendering (chat, surfaces, plan cards) ✅
- WebSocket surface delivery ✅
- Trust state recording (email.read graduated) ✅

### What was found broken:
- **This issue** — inline execution bypasses TrustEngine for write capabilities
- Plan.status stays "created" after inline execution completes (no transition to "completed")

### Key files to start with:
- `backend/src/orchestrator/jarvis.py:1159-1217` — the inline step execution loop (the fix location)
- `backend/src/services/trust_engine.py` — TrustEngine.evaluate() to reuse
- `backend/src/services/risk_assessor.py` — get_or_assess_risk() to reuse
- `backend/src/services/graph_executor.py:700-901` — reference implementation of the approval gate in GraphExecutor._execute_step()

### Auth for testing:
- Email: `admin@jarvis.local` (has Gmail integration)
- Magic link flow: `POST /v1/auth/magic-link` → `POST /v1/auth/verify` → Bearer token
- Frontend: inject token via `localStorage.setItem('jarvis_auth_token', '<token>')`

### Backend is running:
- `python run.py --worker` on port 8000 (PID 47026)
- Frontend on port 3000
- All infra up: Postgres, Redis, Qdrant, Neo4j, MinIO, Google Workspace MCP
