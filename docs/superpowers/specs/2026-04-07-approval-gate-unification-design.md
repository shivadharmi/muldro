# Spec 2B: Approval Gate Unification + Trust UI

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 2A (Trust Infrastructure) — needs TrustState, TrustEngine, RiskAssessor, graduation rules
**Builds toward:** Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

Spec 2A built the trust infrastructure (TrustState model, LLM risk assessor, deterministic TrustEngine, graduation rules, feedback loop). This spec **wires it in** — replacing the triple approval gate with a single gate, deleting dead code (ApprovalPolicyEngine, TrustScore, Governor agent), absorbing policy modes, and building the frontend Trust UI.

This is the high-risk half: every change modifies existing approval behavior.

## Design

### Component 1: Single Approval Gate in GraphExecutor

Replace the dual check in `_execute_step()` (per-tool `requires_approval` flag + `ApprovalPolicyEngine`) with a single `TrustEngine` call:

```python
async def _execute_step(self, run: TaskRun, step: TaskStep) -> None:
    if step.status == "running":  # Resumed after approval
        ...
        return

    capability = (step.input_data or {}).get("capability", "")

    # Single gate: LLM risk assessment (Haiku, cached) → TrustEngine decision
    risk = await get_or_assess_risk(
        capability=capability,
        step_input=step.input_data,
        user_context=await self._get_user_context(run),
        workspace_id=run.workspace_id,
    )
    decision = await self._trust_engine.evaluate(capability, risk)

    if decision.decision == "approval_required":
        await self._create_approval_and_pause(run, step, risk, decision)
        return
    elif decision.decision == "auto_execute_notify":
        output = await self._run_step_action(step, run)
        await self._notify_auto_executed(run, step, risk, output)
    elif decision.decision == "auto_execute_silent":
        output = await self._run_step_action(step, run)
    else:
        await self._create_approval_and_pause(run, step, risk, decision)
        return
```

### Component 2: Convert Governor Hook to Audit-Only

In `hooks.py`, `governor_pre_tool_hook` stops creating approvals. It becomes an audit logger only:

```python
async def governor_pre_tool_hook(tool_name, tool_input, agent_name, ...) -> dict:
    # Classify tool for audit (keep existing classification logic)
    # But ALWAYS return allowed: True
    # The TrustEngine in GraphExecutor handles approval gating now

    # Log the tool call for audit
    logger.info("tool_audit", extra={"tool": tool_name, "agent": agent_name, "risk": risk_level})

    return {"allowed": True}  # Always allow — gating moved to GraphExecutor
```

### Component 3: Delete Dead Systems

| File | Action |
|------|--------|
| `src/services/approval_policy_engine.py` | **DELETE** |
| `src/models/trust_score.py` | **DELETE** |
| `src/models/approval_policy.py` | **DELETE** |
| Alembic migration | Drop `approval_policies` + `trust_scores` tables |

### Component 4: Governor Agent Demotion

Governor is no longer an LLM agent in the pipeline. It's demoted to edge-case fallback only:

- Remove `governor` from default agent pipelines (already done in Spec 1B — no RouteResolver)
- Simplify `GOVERNOR_PROMPT` — only handles novel/ambiguous risk situations
- Governor LLM call triggers only when risk assessor returns low confidence or capability is unknown
- Update `agents.py` — keep `governor` in AGENTS dict but mark as `edge_case_only: True`

### Component 5: Policy Mode Absorption

Map existing 4 policy modes to workspace-level trust ceilings:

| Policy Mode | Trust Ceiling Effect |
|---|---|
| `lockdown` | All capabilities → `blocked` |
| `approval_required` | All capabilities → `learning` |
| `suggest_only` | All capabilities → `first_use` |
| `full_auto` | No ceiling restriction |

Update `PUT /v1/settings/policy/mode` to batch-update `TrustCeiling` records. Settings → Policy UI keeps the 4-mode selector (simple UX) backed by trust ceilings underneath.

### Component 6: Approval UX Changes

Approval surfaces adapt based on trust level:

- **first_use:** Full context, "first time" label, complete preview
- **learning:** Condensed context, "similar to N approvals", graduation hint
- **trusted (auto-executed):** Post-execution notification with undo button
- **autonomous (silent):** Activity feed entry only

### Component 7: Trust Transparency UI (Frontend)

New Settings → Trust tab:

- Per-capability trust levels grouped by family (Email, Calendar, GitHub, etc.)
- Per-risk-level breakdown within each capability
- Graduation progress (e.g., "4/10 approvals to trusted")
- Ceiling controls per capability
- Reset trust button

### Component 8: New Trust API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET /v1/trust/dashboard` | All capabilities with levels, progress, ceilings |
| `GET /v1/trust/{capability}` | Detailed trust state across risk levels |
| `PUT /v1/trust/{capability}/ceiling` | Set max trust level |
| `POST /v1/trust/{capability}/reset` | Reset trust scores |
| `GET /v1/trust/time-policies` | List time-based overrides |
| `PUT /v1/trust/time-policies` | Set time-based overrides |

### Component 9: Dead Code — Time-Based Policies

Move `_get_time_based_policy_override()` from Governor to TrustEngine as time-scoped ceilings. Expose via API (Component 8).

## Files Changed

### Deleted Files
- `src/services/approval_policy_engine.py`
- `src/models/trust_score.py`
- `src/models/approval_policy.py`

### Modified Files — Backend
- `src/services/graph_executor.py` — Single TrustEngine gate in `_execute_step()`
- `src/orchestrator/hooks.py` — Convert `governor_pre_tool_hook` to audit-only
- `src/orchestrator/agent_loop.py` — Hook now always returns `allowed: True`
- `src/services/governor.py` — Demote to edge-case fallback, remove plan-level approval
- `src/orchestrator/prompts.py` — Simplify GOVERNOR_PROMPT
- `src/orchestrator/agents.py` — Mark governor as edge-case
- `src/runtime.py` — Initialize TrustEngine in service container
- `src/services/notifier.py` — Handle `auto_execute_notify` notification type
- `src/services/surface_builder.py` — Trust context in approval surfaces
- `src/services/surface_detail_builders.py` — Graduation progress in detail views
- `src/orchestrator/recovery.py` — Verify TrustState compatibility
- `src/api/routes_settings.py` — Policy mode → trust ceiling mapping

### New Files
- `src/api/routes_trust.py` — 6 trust transparency endpoints
- Alembic migration to drop `approval_policies` + `trust_scores` tables
- `tests/test_trust_api.py`

### Modified Files — Frontend
- `frontend/src/app/settings/page.tsx` — New Trust tab
- `frontend/src/lib/types.ts` — TrustState, TrustCeiling types, updated ApprovalDetail
- `frontend/src/lib/api.ts` — Trust dashboard/ceiling/reset API calls
- `frontend/src/components/workspace/surface-card.tsx` — Trust context in approval rendering
- `frontend/src/stores/activity-store.ts` — `auto_execute_notify` events

## Testing Strategy

- Unit tests: single gate in GraphExecutor — approval_required, auto_execute_notify, auto_execute_silent paths
- Unit tests: governor_pre_tool_hook always returns allowed:True
- Unit tests: policy mode → trust ceiling mapping
- Integration: approve 10 low-risk → verify auto_execute_notify kicks in
- Integration: reject at trusted → demotion → next action requires approval
- Integration: set ceiling → verify trust can't exceed it
- E2E: trust dashboard returns correct state for each capability
- Frontend: Trust tab renders, ceiling control works

## Success Criteria

1. Single approval gate — no triple prompting
2. governor_pre_tool_hook is audit-only (always allows)
3. ApprovalPolicyEngine and TrustScore deleted
4. Policy modes map to trust ceilings
5. Frontend Trust tab shows per-capability graduation progress
6. Auto-executed actions notify user (trusted level)
7. Trust API endpoints functional

## Blast Radius

**High — modifies core approval flow.**

### Tier 1: CRITICAL
| File | What changes | Why |
|------|-------------|-----|
| `src/services/graph_executor.py` | Replace dual approval check with TrustEngine | Central execution |
| `src/orchestrator/hooks.py` | Convert to audit-only | Agent loop calls this on every tool |
| `src/services/governor.py` | Demote to edge-case | Was primary approval evaluator |

### Tier 2: HIGH
| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/agent_loop.py` | Hook always allows | Behavior change |
| `src/runtime.py` | TrustEngine initialization | Service container |
| `src/services/notifier.py` | New notification type | Delivery |
| `src/api/routes_settings.py` | Policy→ceiling mapping | Settings |

### Tier 3: Tests (12 files need rewrite)
Governor tests, approval tests, hook tests, executor tests, contract tests — all need updating for new behavior.

### Total: ~35 files (15 source, 12 tests, 3 deleted, 5 frontend)
