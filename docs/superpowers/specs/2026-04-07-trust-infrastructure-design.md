# Spec 2A: Trust Infrastructure

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B (Planner Rewrite) — needs capability-level steps for per-capability trust
**Builds toward:** Spec 2B (Approval Gate Unification + Trust UI)

## Problem Statement

The approval system needs a trust data layer before the triple gate can be unified. This spec builds the **infrastructure** — new models, LLM risk assessor, deterministic trust engine, graduation rules, and feedback loop — without modifying any existing approval gates. Everything here is additive or new services.

See parent problem: triple approval gates, no trust graduation, context-blind approval.

## Design

### Core Principle: LLM Understands, Code Decides

1. **Understanding** (LLM): Haiku assesses contextual risk — who is affected, what could go wrong, how reversible.
2. **Deciding** (deterministic code): Trust state lookup for capability × risk level → predictable decision.

### Component 1: LLM Risk Assessor

New file: `src/services/risk_assessor.py`

Focused Haiku call that evaluates contextual risk for any action.

**System prompt:**
```
You assess the contextual risk of actions Jarvis is about to perform
on behalf of the user.

Consider:
- What could go wrong if this action is incorrect or premature?
- Is this reversible? Can it be undone?
- What's the blast radius? Who and how many are affected?
- How sensitive is the content being acted on?
- What's the relationship context? (casual, professional, critical)

You receive:
- The capability being used and its parameters
- The user's goals, relationships, and recent context (from memory)

Output JSON only:
{
  "risk_level": "none | low | medium | high",
  "reasoning": "1-2 sentence human-readable explanation",
  "reversible": true | false,
  "blast_radius": "self | internal | external_single | external_multiple | public"
}
```

**Implementation:**
```python
class RiskAssessment(BaseModel):
    risk_level: Literal["none", "low", "medium", "high"]
    reasoning: str
    reversible: bool = True
    blast_radius: Literal["self", "internal", "external_single", "external_multiple", "public"] = "self"

async def assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    client: Any,
    model: str = "haiku",
) -> RiskAssessment:
    ...
```

### Component 2: Risk Assessment Caching

Redis-backed cache for repeated similar actions.

- **Cache key:** `(capability, recipient_or_target_hash, content_similarity_bucket)`
- **Cache TTL:** 24 hours
- **Invalidation:** On trust state change for the capability

```python
async def get_or_assess_risk(capability, step_input, user_context, workspace_id, ...) -> RiskAssessment:
    cache_key = build_risk_cache_key(capability, step_input)
    cached = await redis.get(f"risk:{workspace_id}:{cache_key}")
    if cached:
        return RiskAssessment.model_validate_json(cached)
    assessment = await assess_risk(...)
    await redis.setex(f"risk:{workspace_id}:{cache_key}", 86400, assessment.model_dump_json())
    return assessment
```

### Component 3: TrustState Model

New file: `src/models/trust_state.py`

```python
class TrustState(Base):
    __tablename__ = "trust_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    approved_count: Mapped[int] = mapped_column(default=0)
    rejected_count: Mapped[int] = mapped_column(default=0)
    modified_count: Mapped[int] = mapped_column(default=0)
    trust_level: Mapped[str] = mapped_column(default="first_use")
    last_decision_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "capability", "risk_level"),
        Index("ix_trust_state_lookup", "workspace_id", "capability", "risk_level"),
    )

class TrustCeiling(Base):
    __tablename__ = "trust_ceilings"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    max_level: Mapped[str] = mapped_column(String, default="autonomous")

    __table_args__ = (UniqueConstraint("workspace_id", "capability"),)
```

### Component 4: Trust Graduation Rules

```python
def graduate_trust(state: TrustState) -> str:
    if state.cooldown_until and datetime.now(timezone.utc) < state.cooldown_until:
        return state.trust_level
    total = state.approved_count + state.rejected_count
    if total == 0:
        return "first_use"
    rejection_rate = state.rejected_count / total
    if state.approved_count >= 25 and rejection_rate < 0.05:
        return "autonomous"
    elif state.approved_count >= 10 and rejection_rate < 0.10:
        return "trusted"
    elif state.approved_count >= 3 and state.rejected_count == 0:
        return "learning"
    return "first_use"

def apply_rejection(state: TrustState) -> None:
    state.rejected_count += 1
    now = datetime.now(timezone.utc)
    if state.trust_level == "autonomous":
        state.trust_level = "trusted"
        state.cooldown_until = now + timedelta(hours=72)
    elif state.trust_level == "trusted":
        state.trust_level = "learning"
        state.cooldown_until = now + timedelta(hours=48)
    elif state.trust_level == "learning":
        state.trust_level = "first_use"
        state.cooldown_until = now + timedelta(hours=24)
```

### Component 5: Deterministic TrustEngine

New file: `src/services/trust_engine.py` (rewrite of existing)

```python
class TrustEngine:
    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def evaluate(self, capability: str, risk_assessment: RiskAssessment) -> PolicyDecision:
        risk = risk_assessment.risk_level
        state = await self._get_trust_state(capability, risk)
        ceiling = await self._get_ceiling(capability)
        effective_level = min_trust_level(state.trust_level, ceiling.max_level)

        if effective_level in ("first_use", "learning"):
            return PolicyDecision(decision="approval_required", justification=risk_assessment.reasoning, risk_level=risk)
        elif effective_level == "trusted":
            if risk in ("none", "low"):
                return PolicyDecision(decision="auto_execute_notify", ...)
            return PolicyDecision(decision="approval_required", ...)
        elif effective_level == "autonomous":
            if risk == "high":
                return PolicyDecision(decision="approval_required", ...)
            elif risk == "medium":
                return PolicyDecision(decision="auto_execute_notify", ...)
            return PolicyDecision(decision="auto_execute_silent", ...)
        return PolicyDecision(decision="approval_required", risk_level=risk)
```

### Component 6: Trust Feedback Loop

```python
async def record_approval_decision(db, workspace_id, capability, risk_level, decision):
    state = await get_or_create_trust_state(db, workspace_id, capability, risk_level)
    if decision == "approved":
        state.approved_count += 1
    elif decision == "rejected":
        apply_rejection(state)
    elif decision == "modified":
        state.modified_count += 1
        state.approved_count += 1
    state.last_decision_at = datetime.now(timezone.utc)
    state.trust_level = graduate_trust(state)
    await db.flush()
```

### Component 7: PolicyDecision Contract Extension

Add `auto_execute_notify` and `auto_execute_silent` to `PolicyDecision.decision` Literal in `contracts.py`.

## Absorbed Issues from Audit

**Issue #13 — MCP no cost tracking per tool call:** Add per-capability cost tracking. In `agent_loop.py`, after each tool call, record cost. Expose in trust dashboard.

## Files Changed

### New Files
- `src/services/risk_assessor.py` — LLM risk assessment + caching
- `src/models/trust_state.py` — TrustState + TrustCeiling models
- Alembic migration for `trust_states` and `trust_ceilings` tables
- `tests/test_risk_assessor.py`
- `tests/test_trust_graduation.py`
- `tests/test_trust_engine_v2.py`

### Modified Files (Additive)
- `src/orchestrator/contracts.py` — Extend `PolicyDecision.decision` Literal with 2 new values
- `src/services/trust_engine.py` — Rewrite to use TrustState model + graduation rules (existing file, new logic)
- `src/api/routes_approvals.py` — Add `record_approval_decision()` call in approve/reject handlers
- `src/orchestrator/agent_loop.py` — Add per-tool cost attribution call

### NOT Modified (saved for Spec 2B)
- `src/orchestrator/hooks.py` — governor_pre_tool_hook untouched
- `src/services/graph_executor.py` — approval gate untouched
- `src/services/governor.py` — Governor agent untouched
- `src/services/approval_policy_engine.py` — not deleted yet
- `src/models/trust_score.py` — not deleted yet
- All frontend files — untouched

## Testing Strategy

- Unit tests: `graduate_trust()` all paths (first_use→learning→trusted→autonomous, cooldown, edge cases)
- Unit tests: `apply_rejection()` demotion + cooldown timing
- Unit tests: `TrustEngine.evaluate()` all 16 combinations (4 trust levels × 4 risk levels)
- Unit tests: risk assessment caching (hit, miss, invalidation)
- Unit tests: `record_approval_decision()` approve/reject/modified flows
- Integration: approve 3 times → trust graduates to learning
- Integration: reject at trusted → drops to learning with 48h cooldown

## Success Criteria

1. TrustState model tracks per-capability × risk-level trust
2. LLM risk assessor returns contextual risk via Haiku (~200ms)
3. Risk assessment caching avoids redundant LLM calls
4. TrustEngine produces deterministic decisions from trust state + risk
5. Trust feedback loop updates state on approve/reject
6. Graduation rules work correctly (thresholds, cooldowns)
7. Existing approval system continues working unchanged

## Blast Radius

**Low — mostly new files + additive contract changes.**

| File | Change | Risk |
|------|--------|------|
| `src/orchestrator/contracts.py` | Add 2 values to PolicyDecision Literal | **LOW** — additive |
| `src/services/trust_engine.py` | Rewrite internals (new model) | **MEDIUM** — existing tests need update |
| `src/api/routes_approvals.py` | Add feedback call in approve/reject | **LOW** — additive |
| `src/orchestrator/agent_loop.py` | Add cost attribution | **LOW** — additive |

### Total: ~15 files (4 modified, 6 new source, 5 new tests)
