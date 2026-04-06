# Spec 2: Unified Approval & Trust Graduation

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1 (Intelligent Planner) — needs capability-level steps for per-capability trust
**Builds toward:** Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

The current approval system has three structural problems:

1. **Triple approval gates.** Three separate mechanisms enforce approval independently:
   - Governor LLM agent evaluates the entire plan (expensive, non-deterministic)
   - `governor_pre_tool_hook` in `hooks.py` intercepts every tool call based on `requires_approval` flag
   - GraphExecutor `_execute_step` checks per-tool and per-policy approval before each DAG step

   These don't coordinate. The user can get prompted at all three levels for the same action.

2. **No trust graduation.** The Governor prompt hardcodes "NEVER auto-approve external writes in v1." The `TrustScore` model exists in the DB but is not integrated into any approval decision. Every interaction is treated as day zero — no learning from approval history.

3. **Context-blind approval.** The current system treats all instances of a capability identically. Sending a casual email to a friend and sending financial projections to an investor both trigger the same approval flow. Risk assessment is based on the tool's static `risk_level` and `requires_approval` flag, not on the actual context of the action.

### Soul/Vision Alignment Issues

- **Soul:** "Earn increasing trust through behavior, not claims" — not implemented
- **Soul:** "Autonomy should feel like a carefully widened lane, not a sudden leap" — approval is a wall, not a lane
- **Soul:** "Reduce cognitive load" — approval fatigue from triple gates
- **Soul:** "Good initiative feels like relief. Bad initiative feels like interference" — blanket approval feels like interference

## Design

### Core Principle: LLM Understands, Code Decides

Every approval evaluation has two phases:
1. **Understanding** (LLM): Assess the contextual risk of this specific action — who is affected, what could go wrong, how reversible, how sensitive. This requires nuance that rules can't provide.
2. **Deciding** (deterministic code): Look up trust state for this capability at this risk level, apply graduation rules, produce a predictable decision.

### Component 1: LLM Risk Assessor

A focused, constrained Haiku call that evaluates contextual risk for any action.

**Model:** Haiku (fast ~200ms, cheap ~$0.001/call)
**Purpose:** Understand the stakes of a specific action in context

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

**Input construction:**
```python
async def assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,   # goals, entity relationships, recent activity
    client: Any,          # Anthropic client
    model: str = "haiku", # Always Haiku
) -> RiskAssessment:
    """Assess contextual risk of an action via LLM."""

    message = f"""Capability: {capability}
Parameters: {json.dumps(step_input, indent=2)}

User context:
- Goals: {user_context.get('goals', 'none')}
- Relevant entities: {json.dumps(user_context.get('entities', []))}
- Recent activity: {user_context.get('recent_activity', 'none')}

Assess the risk of this action."""

    response = await client.messages.create(
        model=model,
        max_tokens=256,
        system=RISK_ASSESSOR_PROMPT,
        messages=[{"role": "user", "content": message}],
    )
    return RiskAssessment.model_validate_json(response.content[0].text)
```

**Examples of LLM understanding that rules can't match:**

| Action | Rule-based would say | LLM understands |
|---|---|---|
| "Hey want to grab lunch?" to investor | "Investor → high risk" | "Casual social message, low stakes" |
| "Revenue projections attached" to friend | "Friend → low risk" | "Sensitive financial data to personal contact, potential data leak" |
| "LGTM" on internal PR | "PR → medium" | "Routine code review, minimal impact" |
| "LGTM, merging to main" on public repo | "PR → medium" | "Public repo merge, affects external users" |
| Birthday message in #general | "Broadcast → high" | "Social message, zero business impact" |

### Component 2: Risk Assessment Caching

Not every action needs a fresh LLM call. Cache assessments for similar actions.

**Cache key:** `(capability, recipient_or_target_hash, content_similarity_bucket)`
**Cache TTL:** 24 hours
**Cache storage:** Redis with workspace-scoped keys

```python
async def get_or_assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    workspace_id: str,
    ...
) -> RiskAssessment:
    cache_key = build_risk_cache_key(capability, step_input)
    cached = await redis.get(f"risk:{workspace_id}:{cache_key}")
    if cached:
        return RiskAssessment.model_validate_json(cached)

    assessment = await assess_risk(capability, step_input, user_context, ...)
    await redis.setex(f"risk:{workspace_id}:{cache_key}", 86400, assessment.model_dump_json())
    return assessment
```

**Cache invalidation:** On any trust state change (approval, rejection) for the capability.

### Component 3: Trust State Model

Trust accumulates per **capability x risk level** per workspace.

```python
class TrustState(Base):
    """Tracks trust graduation per capability per risk level."""

    __tablename__ = "trust_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)      # e.g., "email.send"
    risk_level: Mapped[str] = mapped_column(String, nullable=False)      # none, low, medium, high

    approved_count: Mapped[int] = mapped_column(default=0)
    rejected_count: Mapped[int] = mapped_column(default=0)
    modified_count: Mapped[int] = mapped_column(default=0)               # user edited before approving

    trust_level: Mapped[str] = mapped_column(default="first_use")        # first_use, learning, trusted, autonomous
    last_decision_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(nullable=True)  # after rejection, freeze level

    # Constraints
    __table_args__ = (
        UniqueConstraint("workspace_id", "capability", "risk_level"),
        Index("ix_trust_state_lookup", "workspace_id", "capability", "risk_level"),
    )
```

**Separate from the existing `TrustScore` model** (which tracks per-action-type, conflates tool trust and behavioral trust). The old model is deprecated — migration converts existing data where possible.

### Component 4: Trust Graduation Rules

```
Trust Levels:
  first_use → learning → trusted → autonomous

Graduation thresholds:
  first_use → learning:   3+ approvals, 0 rejections
  learning → trusted:     10+ approvals, <10% rejection rate
  trusted → autonomous:   25+ approvals, <5% rejection rate, explicit user opt-in

Rejection impact:
  At learning:    reset to first_use, cooldown 24 hours
  At trusted:     drop to learning, cooldown 48 hours
  At autonomous:  drop to trusted, cooldown 72 hours

Cooldown:
  During cooldown, trust level is locked at the demoted level.
  Cannot graduate during cooldown period.
  Cooldown ensures the system doesn't immediately re-graduate after a rejection.
```

**Implementation:**
```python
def graduate_trust(state: TrustState) -> str:
    """Compute current trust level from counters."""
    now = datetime.now(timezone.utc)

    # Respect cooldown
    if state.cooldown_until and now < state.cooldown_until:
        return state.trust_level  # Locked

    total = state.approved_count + state.rejected_count
    if total == 0:
        return "first_use"

    rejection_rate = state.rejected_count / total if total > 0 else 0

    if state.approved_count >= 25 and rejection_rate < 0.05:
        return "autonomous"
    elif state.approved_count >= 10 and rejection_rate < 0.10:
        return "trusted"
    elif state.approved_count >= 3 and state.rejected_count == 0:
        return "learning"
    else:
        return "first_use"

def apply_rejection(state: TrustState) -> None:
    """Handle a rejection — demote trust level with cooldown."""
    state.rejected_count += 1
    current = state.trust_level
    now = datetime.now(timezone.utc)

    if current == "autonomous":
        state.trust_level = "trusted"
        state.cooldown_until = now + timedelta(hours=72)
    elif current == "trusted":
        state.trust_level = "learning"
        state.cooldown_until = now + timedelta(hours=48)
    elif current == "learning":
        state.trust_level = "first_use"
        state.cooldown_until = now + timedelta(hours=24)
```

### Component 5: Workspace Trust Ceiling

Users can set per-capability maximum autonomy in Settings:

```python
class TrustCeiling(Base):
    """User-configured maximum trust level per capability."""

    __tablename__ = "trust_ceilings"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    max_level: Mapped[str] = mapped_column(String, default="autonomous")  # ceiling

    __table_args__ = (
        UniqueConstraint("workspace_id", "capability"),
    )
```

**Example:** User sets `email.send` max_level = "trusted" → even after 25 approvals, email.send never auto-executes silently. The highest it reaches is "trusted" (auto-execute with notification for low-risk, approval for medium+).

**Default ceiling:** `autonomous` (no restriction — trust can fully graduate). Users who want more control lower ceilings.

### Component 6: Deterministic Policy Engine

Replaces the Governor LLM agent for approval decisions.

```python
# New file: src/services/trust_engine.py

class TrustEngine:
    """Deterministic policy engine for approval decisions.

    Replaces the Governor LLM agent. Uses LLM risk assessment as input,
    but the decision itself is deterministic and auditable.
    """

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def evaluate(
        self,
        capability: str,
        risk_assessment: RiskAssessment,
    ) -> PolicyDecision:
        """Evaluate whether an action should proceed, need approval, or be blocked."""

        risk = risk_assessment.risk_level

        # Get trust state for this capability at this risk level
        state = await self._get_trust_state(capability, risk)
        ceiling = await self._get_ceiling(capability)

        effective_level = min_trust_level(state.trust_level, ceiling.max_level)

        # Decision matrix
        if effective_level == "first_use":
            return PolicyDecision(
                decision="approval_required",
                justification=risk_assessment.reasoning,
                risk_level=risk,
                context="first_use",
            )

        elif effective_level == "learning":
            return PolicyDecision(
                decision="approval_required",
                justification=risk_assessment.reasoning,
                risk_level=risk,
                context="learning",
            )

        elif effective_level == "trusted":
            if risk in ("none", "low"):
                return PolicyDecision(
                    decision="auto_execute_notify",
                    justification=risk_assessment.reasoning,
                    risk_level=risk,
                )
            else:
                return PolicyDecision(
                    decision="approval_required",
                    justification=risk_assessment.reasoning,
                    risk_level=risk,
                )

        elif effective_level == "autonomous":
            if risk == "high":
                return PolicyDecision(
                    decision="approval_required",
                    justification=risk_assessment.reasoning,
                    risk_level=risk,
                )
            elif risk == "medium":
                return PolicyDecision(
                    decision="auto_execute_notify",
                    justification=risk_assessment.reasoning,
                    risk_level=risk,
                )
            else:
                return PolicyDecision(
                    decision="auto_execute_silent",
                    justification=risk_assessment.reasoning,
                    risk_level=risk,
                )

        # Fallback: require approval
        return PolicyDecision(decision="approval_required", risk_level=risk)
```

### Component 7: Single Approval Gate

Replace the three current gates with one, at the step level in GraphExecutor.

**Delete:**
- Governor as an LLM agent for approval decisions (keep the prompt for edge cases, see below)
- `governor_pre_tool_hook` as an approval gate (convert to audit-only hook)
- Separate step-level approval logic in `_execute_step` (replaced by TrustEngine call)

**The single gate:**
```python
# In GraphExecutor._execute_step()

async def _execute_step(self, run: TaskRun, step: TaskStep) -> None:
    # Skip gate if already approved (resumed after approval)
    if step.status == "running":
        # Execute directly
        ...
        return

    # === SINGLE APPROVAL GATE ===
    capability = (step.input_data or {}).get("capability", "")

    # Step 1: LLM risk assessment (Haiku, cached)
    risk = await get_or_assess_risk(
        capability=capability,
        step_input=step.input_data,
        user_context=await self._get_user_context(run),
        workspace_id=run.workspace_id,
    )

    # Step 2: Deterministic trust decision
    decision = await self._trust_engine.evaluate(capability, risk)

    # Step 3: Act on decision
    if decision.decision == "approval_required":
        await self._create_approval_and_pause(run, step, risk, decision)
        return
    elif decision.decision == "auto_execute_notify":
        # Execute, then notify user after
        output = await self._run_step_action(step, run)
        await self._notify_auto_executed(run, step, risk, output)
    elif decision.decision == "auto_execute_silent":
        # Execute silently, log only
        output = await self._run_step_action(step, run)
    else:
        # approval_required fallback
        await self._create_approval_and_pause(run, step, risk, decision)
        return
```

### Component 8: Trust Feedback Loop

After every approval decision, update trust state:

```python
async def record_approval_decision(
    db: AsyncSession,
    workspace_id: str,
    capability: str,
    risk_level: str,
    decision: Literal["approved", "rejected", "modified"],
) -> None:
    """Update trust state after user's approval decision."""

    state = await get_or_create_trust_state(db, workspace_id, capability, risk_level)

    if decision == "approved":
        state.approved_count += 1
    elif decision == "rejected":
        apply_rejection(state)
    elif decision == "modified":
        state.modified_count += 1
        state.approved_count += 1  # Modified counts as approved but tracked separately

    state.last_decision_at = datetime.now(timezone.utc)
    state.trust_level = graduate_trust(state)
    await db.flush()
```

**Wired into:** `routes_approvals.py` — both approve and reject endpoints call `record_approval_decision`.

### Component 9: Approval UX Changes

The approval surface adapts based on trust level:

**first_use:**
```
⚠ Approval needed (first time)

Send email to sarah@vc.com
Subject: "Q2 Revenue Projections"

Why: Sharing financial projections with external investor.
     Contains sensitive revenue data. Not reversible once sent.

This is the first time Jarvis is sending email on your behalf.

[Full email preview below]

[Approve]  [Edit first]  [Reject]
```

**learning:**
```
⚠ Approval needed

Send email to sarah@vc.com
Subject: "Q2 Revenue Projections"

Why: Financial data to external investor. Not reversible.

Similar to 4 emails you've approved before.
~6 more approvals until this type auto-executes.

[Approve]  [Edit]  [Reject]
```

**trusted (auto-executed, notification):**
```
✓ Email sent to sarah@vc.com
  Subject: "Q2 Revenue Projections"
  
  [Undo within 30s]  [View email]
```

**autonomous (silent, visible in activity feed only):**
```
Activity feed entry:
  10:32am — Sent email to sarah@vc.com: "Q2 Revenue Projections"
```

### Component 10: Trust Transparency in Settings

New Settings → Trust tab:

```
Trust Settings

Email
  ├─ email.search ........... autonomous (45 approvals, 0 rejections)
  ├─ email.read ............. autonomous (38 approvals, 0 rejections)  
  ├─ email.draft ............ trusted (12 approvals, 1 rejection)
  ├─ email.send (low risk) .. learning (4 approvals, 0 rejections)
  ├─ email.send (med risk) .. first_use (1 approval, 0 rejections)
  └─ email.send (high risk) . first_use (0 approvals)
  
  Max autonomy: trusted  [Change]
  (email.send will never auto-execute silently)

Calendar
  ├─ calendar.read .......... autonomous
  ├─ calendar.write (low) ... trusted (8 approvals)
  └─ calendar.write (high) .. learning (3 approvals)
  
  Max autonomy: autonomous  [Change]

[Reset all trust]  [Export trust data]
```

### Edge Case: Governor LLM for Novel Situations

The deterministic policy engine handles 95% of cases. For truly novel situations where the risk assessment is ambiguous (LLM returns low confidence, or the capability is unknown), fall back to a Governor LLM call.

**When to invoke Governor LLM:**
- Risk assessor returns ambiguous result (add `confidence` field to `RiskAssessment`)
- Capability is unknown (not in tool registry)
- Multiple capabilities in a single step with conflicting risk levels

This should be rare (<5% of evaluations). The Governor LLM prompt is simplified — it only handles edge cases, not routine approval.

## Files Changed

### New Files
- `src/services/trust_engine.py` — Deterministic policy engine
- `src/services/risk_assessor.py` — LLM risk assessment with caching
- `src/models/trust_state.py` — TrustState and TrustCeiling models
- Alembic migration for `trust_states` and `trust_ceilings` tables

### Modified Files
- `src/services/graph_executor.py` — Single approval gate via TrustEngine
- `src/orchestrator/hooks.py` — Convert `governor_pre_tool_hook` to audit-only (remove approval gate)
- `src/api/routes_approvals.py` — Wire `record_approval_decision` into approve/reject endpoints
- `src/orchestrator/agents.py` — Remove Governor agent (or demote to edge-case only)
- `src/orchestrator/prompts.py` — Simplify Governor prompt for edge cases only
- `src/orchestrator/contracts.py` — Add `auto_execute_notify` and `auto_execute_silent` to PolicyDecision
- `src/api/routes_settings.py` — Trust transparency endpoints
- Frontend: Settings page with Trust tab

### Deleted
- `src/models/trust_score.py` — Replaced by `trust_state.py`
- `src/services/approval_policy_engine.py` — Replaced by `trust_engine.py`
- Governor agent as a pipeline step in routes (demoted to edge-case fallback)

## Testing Strategy

- Unit tests for `graduate_trust()` — all graduation paths, edge cases
- Unit tests for `apply_rejection()` — cooldown timing, demotion rules
- Unit tests for `TrustEngine.evaluate()` — all combinations of trust level x risk level
- Unit tests for risk assessment caching — cache hit, miss, invalidation
- Integration tests for trust feedback loop — approve → trust increments → graduation
- Integration tests for rejection flow — reject → demotion → cooldown → recovery
- Integration test for ceiling enforcement — trust graduates but respects ceiling
- E2E test: approve 3 low-risk emails → verify trust graduates to learning
- E2E test: approve 10 low-risk emails → verify auto-execute with notification

## Success Criteria

1. Single approval gate — no triple prompting
2. Trust graduates based on approval history — user isn't stuck at day zero
3. Contextual risk assessment — friend emails and investor emails are treated differently
4. Approval UX shows WHY and shows graduation progress
5. User can control trust ceilings per capability
6. System is faster — deterministic engine replaces Governor LLM call for 95%+ of evaluations
