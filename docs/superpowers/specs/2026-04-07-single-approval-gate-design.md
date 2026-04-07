# Spec 2B-i: Single Approval Gate + Hook Conversion

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 2A (Trust Infrastructure) — needs TrustState, TrustEngine, RiskAssessor
**Builds toward:** Spec 2B-ii (Trust UI + Policy Cleanup)

## Problem Statement

Spec 2A built the trust infrastructure. This spec wires it in as the **single approval gate** — replacing the triple gate (Governor LLM + pre-tool hook + step-level check) with one TrustEngine call in the GraphExecutor. The governor_pre_tool_hook becomes audit-only. The Governor agent is demoted to edge-case fallback.

This is the backend switchover. No frontend changes, no dead code deletion — those are in Spec 2B-ii.

## Design

### Component 1: Single Gate in GraphExecutor

Replace dual approval check in `_execute_step()` with single TrustEngine call:

```python
async def _execute_step(self, run, step):
    if step.status == "running":  # Resumed after approval
        ...
        return

    capability = (step.input_data or {}).get("capability", "")
    risk = await get_or_assess_risk(capability, step.input_data, ...)
    decision = await self._trust_engine.evaluate(capability, risk)

    if decision.decision == "approval_required":
        await self._create_approval_and_pause(run, step, risk, decision)
        return
    elif decision.decision == "auto_execute_notify":
        output = await self._run_step_action(step, run)
        await self._notify_auto_executed(run, step, risk, output)
    elif decision.decision == "auto_execute_silent":
        output = await self._run_step_action(step, run)
```

Remove: `ApprovalPolicyEngine` import and call (lines 539-568). Remove: per-tool `requires_approval` flag check (lines 531-536).

### Component 2: Convert Hook to Audit-Only

`governor_pre_tool_hook` in `hooks.py` stops creating approvals:

```python
async def governor_pre_tool_hook(tool_name, tool_input, agent_name, ...) -> dict:
    # Classify for audit logging (keep existing classification)
    # But ALWAYS return allowed: True
    logger.info("tool_audit", extra={"tool": tool_name, "agent": agent_name})
    return {"allowed": True}
```

Remove: approval creation logic (lines 99-163). Keep: audit logging.

### Component 3: Governor Agent Demotion

- Simplify `GOVERNOR_PROMPT` — only handles novel/ambiguous situations
- Governor LLM call triggers only when risk assessor confidence is low or capability unknown
- Mark `governor` as `edge_case_only: True` in agents.py config
- Remove governor from any remaining pipeline positions

### Component 4: Notifier `auto_execute_notify`

Add handling for the new `auto_execute_notify` notification type:

```python
# In notifier.py
if notification_type == "auto_execute_notify":
    # Post-execution notification — show what was done + undo option
    title = f"✓ {action_description}"
    body = f"Auto-executed. {risk_reasoning}"
    # Deliver to active surfaces with lower priority than approval_request
```

### Component 5: TrustEngine Initialization

Wire TrustEngine into the service container:

```python
# In runtime.py
trust_engine = TrustEngine(db, workspace_id)
# Pass to GraphExecutor
executor = GraphExecutor(..., trust_engine=trust_engine)
```

## Files Changed

### Modified Files (10)
- `src/services/graph_executor.py` — Single TrustEngine gate, remove ApprovalPolicyEngine + requires_approval checks
- `src/orchestrator/hooks.py` — Convert to audit-only (remove lines 99-163)
- `src/orchestrator/agent_loop.py` — Hook always returns `allowed: True`
- `src/services/governor.py` — Demote, simplify for edge-case only
- `src/orchestrator/prompts.py` — Simplify GOVERNOR_PROMPT
- `src/orchestrator/agents.py` — Mark governor edge_case_only
- `src/runtime.py` — TrustEngine initialization
- `src/services/notifier.py` — Handle `auto_execute_notify`
- `src/orchestrator/recovery.py` — Verify TrustState compatibility
- `src/orchestrator/agent_loop.py` — Add per-tool cost attribution (absorbed issue #13)

### NOT Modified (saved for Spec 2B-ii)
- No files deleted (ApprovalPolicyEngine, TrustScore kept for now)
- No frontend changes
- No new API endpoints
- No policy mode absorption

## Testing Strategy

- Unit tests: single gate — approval_required, auto_execute_notify, auto_execute_silent paths
- Unit tests: hook always returns allowed:True (no approval creation)
- Unit tests: Governor only called when risk confidence < threshold
- Integration: execute write step → TrustEngine evaluates → correct gate behavior
- Integration: approve action → trust state updates (via Spec 2A feedback loop)
- Regression: existing approval flow works for first_use capabilities

## Success Criteria

1. Single approval gate in GraphExecutor — no triple prompting
2. governor_pre_tool_hook is audit-only
3. Governor agent only called for edge cases
4. auto_execute_notify delivers post-execution notification
5. Existing approval creation still works for approval_required decisions

## Blast Radius

**High — modifies core execution gate.**

| File | Change | Risk |
|------|--------|------|
| `src/services/graph_executor.py` | Replace dual check with TrustEngine | **HIGH** — central execution |
| `src/orchestrator/hooks.py` | Remove approval creation | **HIGH** — agent loop calls this |
| `src/services/governor.py` | Demote to edge-case | **MEDIUM** — behavioral change |

### Total: ~18 files (10 modified, 8 test files updated)
