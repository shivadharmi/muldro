# Spec 4: Signal-Driven Proactive Intelligence

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 2 (Trust Graduation) — proposals feed into trust system; Spec 3 (Live Surfaces) — insight surfaces use the surface lifecycle
**Builds on:** Existing perception system (`scheduler.py`, `perception_policy.py`, `perception_state` table)

## Problem Statement

The current perception system detects events across connected sources (Gmail, Calendar, Slack, GitHub) but the path from detection to user value is broken:

1. **No interpretation step.** Detected events go directly to the Planner for decision. The Planner classifies them as a decision type and queues execution. There's no step where the system evaluates: "Is this relevant to the user right now? Does it relate to their goals? Should they care?"

2. **No proposal step.** Background plans execute silently or dump raw approval notifications. The user gets "Approve: send_gmail_message" without context about WHY Jarvis noticed this event or what it means.

3. **Wasteful LLM calls on every interaction.** The Persona agent runs on every single user message (including greetings) to extract preference signals. This costs ~$0.001/call with no proportional value for most interactions.

### Soul/Vision Alignment Issues

- **Soul Initiative Philosophy:** "observe → interpret → surface signal → propose → act within boundaries" — steps 2 and 3 (interpret, surface) are missing
- **Soul:** "Attention is scarce. Jarvis should surface what matters, not compete for presence" — no relevance filtering
- **Soul:** "Good initiative feels like relief. Bad initiative feels like interference" — background execution without proposal feels like interference
- **Vision Pillar #1:** "Continuous Context" — detected signals aren't connected to user goals
- **Vision:** "The winning system will not be the loudest or most theatrical. It will be the most trusted, most useful, and most reliably present" — undifferentiated notifications are loud, not useful

## Design

### Core Principle: Propose Before Acting

Jarvis never acts proactively on detected signals without first proposing to the user. The perception loop follows the soul's initiative philosophy exactly:

```
Observe → Interpret → Surface (propose) → User engages → Act
```

The only exceptions are at `autonomous` trust level for low-risk internal operations (memory updates, briefing compilation, entity extraction).

### Component 1: LLM Relevance Assessor

When perception detects a signal, an LLM evaluates its relevance to the user BEFORE any action is taken.

**Model:** Haiku (fast, cheap — same rationale as risk assessor in Spec 2)
**Purpose:** Answer "Should the user care about this right now?"

**System prompt:**
```
You assess the relevance of detected signals to the user.

You receive:
- The signal (what was detected, from which source)
- The user's active goals (from memory)
- The user's recent activity (what they've been doing)
- The user's entity relationships (who matters to them)
- Historical engagement patterns (did they act on similar signals before?)

Evaluate:
- Does this signal relate to an active goal?
- Is the timing urgent or can it wait?
- How important is the source/sender?
- Is this genuinely new information or noise?
- Based on history, would the user likely engage with this?

Output JSON only:
{
  "relevance_score": 0.0-1.0,
  "reasoning": "1-2 sentence explanation of why this matters (or doesn't)",
  "relates_to_goals": ["goal_id_1", ...],
  "urgency": "immediate | today | this_week | whenever",
  "suggested_actions": [
    {"description": "what the user could do", "capability": "capability.name"}
  ],
  "notification_tier": "push | briefing | silent"
}
```

**Input construction:**
```python
async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,   # goals, entities, recent activity, engagement history
    client: Any,
    model: str = "haiku",
) -> RelevanceAssessment:
    """Assess whether a detected signal is relevant to the user."""

    message = f"""Signal detected:
  Source: {signal.source}
  Type: {signal.event_type}
  Summary: {signal.summary}
  Sender/Actor: {signal.actor}
  Timestamp: {signal.detected_at}

User context:
  Active goals: {json.dumps(user_context.goals)}
  Key relationships: {json.dumps(user_context.key_entities)}
  Recent activity: {user_context.recent_activity_summary}
  
Historical engagement:
  Signals from {signal.source}: {user_context.engagement_stats.get(signal.source, 'no history')}
  Similar signals acted on: {user_context.similar_signal_engagement_rate}

Assess relevance."""

    response = await client.messages.create(
        model=model,
        max_tokens=256,
        system=RELEVANCE_ASSESSOR_PROMPT,
        messages=[{"role": "user", "content": message}],
    )
    return RelevanceAssessment.model_validate_json(response.content[0].text)
```

### Component 2: Notification Tiers

Based on relevance score and urgency, signals route to different tiers:

```
notification_tier = "push"      →  Immediate proactive insight surface
notification_tier = "briefing"  →  Added to next briefing compilation
notification_tier = "silent"    →  Logged to world model, no notification

Threshold mapping:
  relevance >= 0.7 AND urgency in (immediate, today)    → push
  relevance >= 0.4 AND urgency in (today, this_week)    → briefing
  relevance >= 0.4 AND urgency == whenever               → briefing
  relevance < 0.4                                         → silent
```

**Soul alignment:** "Attention is scarce" — only high-relevance signals push immediately. Medium-relevance signals batch into briefings. Low-relevance signals are silently absorbed into the world model (still useful — they update entity state and memory, just don't interrupt the user).

### Component 3: Proactive Insight Surface

When a signal reaches the `push` tier, Jarvis surfaces a proactive insight — NOT an action. It's a proposal.

**Surface structure:**
```python
class InsightSurface:
    surface_id: str              # surf_ULID
    phase: str = "proposal"      # proposal → accepted → executing → completed
    
    # What was detected
    signal_source: str           # "gmail", "github", "calendar"
    signal_summary: str          # "Sarah Chen replied about Series A"
    
    # Why it matters (from LLM relevance assessor)
    relevance_reasoning: str     # "She's asking about Q2 revenue. Relates to your goal: Close Series A by April."
    related_goals: list[str]     # Goal titles that this relates to
    
    # What the user could do
    suggested_actions: list[SuggestedAction]
    
    # Dismissal option
    dismiss_available: bool = True

class SuggestedAction:
    description: str             # "Draft a reply with Q2 projections"
    capability: str              # "email.draft"
    action_input: dict           # Pre-filled input for the action
```

**Rendered as:**
```
┌─────────────────────────────────────────────────┐
│ 💡 Sarah Chen replied about Series A             │
│                                                   │
│ She's asking about your Q2 revenue projections.   │
│ This relates to your goal: "Close Series A        │
│ by April."                                        │
│                                                   │
│ Suggested:                                        │
│ • Draft a reply with Q2 projections               │
│ • Schedule a follow-up call                       │
│                                                   │
│ [Draft reply]  [Schedule call]  [Dismiss]         │
└─────────────────────────────────────────────────┘
```

### Component 4: Proposal → Execution Bridge

When the user clicks a suggested action, the system has explicit intent. The insight surface transitions from `proposal` to the execution surface lifecycle (Spec 3):

```
User clicks "Draft reply"
    ↓
Create PlanOutput from suggested action:
  goal: "Draft reply to Sarah Chen about Q2 projections"
  steps: [
    {capability: "email.read", input: {thread from signal}},
    {capability: "reason", input: {draft reply based on thread + Q2 data}},
    {capability: "email.draft", input: {from reasoning step}},
  ]
    ↓
Surface transitions: proposal → plan_ready → executing → ...
(Same lifecycle as Spec 3)
    ↓
Trust/approval system applies normally (Spec 2)
```

**Key:** The insight surface BECOMES the execution surface. Same surface_id, same card in the workspace. The user sees the seamless transition from "Jarvis noticed something" → "Jarvis is doing something about it."

When the user clicks "Dismiss":
```
Record dismissal:
  signal_source: "gmail"
  signal_type: "reply"
  entity_involved: "Sarah Chen" (or category: "investor")
  dismissed_at: now
    ↓
Update engagement history for future relevance scoring
```

### Component 5: Dismissal Learning

Dismissals are signal about what the user doesn't care about. The system learns from them.

**Engagement tracking model:**
```python
class EngagementHistory(Base):
    """Tracks user engagement with proactive signals for relevance learning."""

    __tablename__ = "engagement_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)

    signal_source: Mapped[str] = mapped_column(String, nullable=False)  # gmail, github, slack
    signal_category: Mapped[str] = mapped_column(String, nullable=False)  # reply, new_issue, mention
    
    engaged_count: Mapped[int] = mapped_column(default=0)    # user clicked a suggested action
    dismissed_count: Mapped[int] = mapped_column(default=0)   # user clicked dismiss
    ignored_count: Mapped[int] = mapped_column(default=0)     # surface shown, no interaction within 4h
    
    engagement_rate: Mapped[float] = mapped_column(default=0.5)  # engaged / (engaged + dismissed + ignored)
    
    last_engaged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_dismissed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    
    suppressed: Mapped[bool] = mapped_column(default=False)   # auto-suppressed after repeated dismissals

    __table_args__ = (
        UniqueConstraint("workspace_id", "signal_source", "signal_category"),
    )
```

**Suppression rules:**
```
After 3+ consecutive dismissals of same (source, category):
  → Lower relevance score by 0.2 for future signals
  
After 5+ consecutive dismissals:
  → Auto-suppress: stop surfacing this signal type
  → Suppressed signals still update world model (silent tier)
  → User can unsuppress in Settings → Preferences

After any engagement on a previously suppressed signal:
  → Remove suppression
  → Reset dismissal counter
```

**Integration with relevance assessor:** The engagement history is passed to the LLM as context:
```
Historical engagement:
  Gmail replies: engaged 8 times, dismissed 2 times (80% engagement)
  GitHub new issues: engaged 1 time, dismissed 6 times (14% engagement) → suppressed
  Slack mentions: engaged 5 times, dismissed 0 times (100% engagement)
```

The LLM uses this to calibrate relevance scores. A signal from a suppressed category starts at a disadvantage — the LLM would need strong goal-alignment to overcome the low engagement history.

### Component 6: Briefing Integration

Signals in the `briefing` tier accumulate and are compiled into the user's next briefing.

**Current briefing system:** `_handle_add_to_brief` stores `briefing_item` memories with 24h TTL. The briefing generator reads these memories.

**Enhancement:** Relevance-assessed signals with `notification_tier: "briefing"` are automatically stored as briefing items:

```python
async def route_signal_to_briefing(
    signal: PerceptionSignal,
    assessment: RelevanceAssessment,
    user_id: str,
    workspace_id: str,
) -> None:
    """Store a medium-relevance signal as a briefing item."""
    await memory_service.store_memory(
        user_id=user_id,
        workspace_id=workspace_id,
        memory_type="briefing_item",
        content=f"{signal.summary}\n\nWhy it matters: {assessment.reasoning}",
        source_event_ids=[signal.event_id],
        ttl_hours=24,
        metadata={
            "relevance_score": assessment.relevance_score,
            "signal_source": signal.source,
            "related_goals": assessment.relates_to_goals,
        },
    )
```

Briefing items now have relevance context, so the briefing generator can sort by relevance and group by goal.

### Component 7: Batched Persona Learning

Replace per-message Persona calls with batched learning.

**Current:** Persona agent runs on EVERY message (fire-and-forget Haiku call). Most interactions yield no meaningful preference signals.

**Proposed:** Persona runs periodically, not per-message.

**Trigger conditions:**
- After every 10 user interactions (batch of recent interactions)
- Daily consolidation (end of day)
- After significant approval patterns (3+ approvals/rejections in same session)

**Batch processing:**
```python
async def run_persona_batch(
    interactions: list[InteractionLog],
    user_id: str,
    workspace_id: str,
) -> list[PreferenceMemory]:
    """Extract preference signals from a batch of interactions."""
    
    # Build summary of recent interactions
    summary = "\n".join([
        f"- {i.message_preview} → {i.intent} → {i.response_preview}"
        for i in interactions
    ])
    
    # Single Persona call for the entire batch
    response = await call_agent(
        "persona",
        message=f"Analyze these {len(interactions)} recent interactions for preference patterns:\n{summary}",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    
    # Store extracted preferences
    ...
```

**Cost savings:** 10 interactions × $0.001/call = $0.01 per-message vs 1 batch call × $0.002 = $0.002. ~5x cheaper with better signal quality (patterns emerge from batches, not individual messages).

### Component 8: Cross-Source Synthesis Improvements

The current cross-source synthesis (when 2+ perception sources have new events in the same scheduler tick) is throttled to 30 minutes and doesn't connect to user goals.

**Enhancement:** Cross-source synthesis uses the relevance assessor:

```python
async def cross_source_synthesis(
    signals: list[PerceptionSignal],
    user_context: UserContext,
) -> list[RelevanceAssessment]:
    """Assess a batch of cross-source signals together."""
    
    # Group signals that might be related
    # e.g., calendar meeting + email from same person + Slack mention
    
    # Single LLM call to assess the batch as a whole
    message = f"""Multiple signals detected across sources:
    {format_signals(signals)}
    
    Are any of these related? Do they together tell a story
    that's more important than any individual signal?
    
    User goals: {user_context.goals}"""
    
    # The LLM can identify: "Sarah Chen emailed about the meeting
    # that's on your calendar tomorrow, and mentioned you in Slack
    # about the same topic. This is convergent signal about your
    # Series A goal."
```

**Throttle change:** Remove the fixed 30-minute cooldown. Instead, rate-limit by signal volume — synthesis runs when 2+ sources have signals AND the total signal count exceeds a threshold (prevents over-synthesis on quiet days, enables fast synthesis on busy days).

### Perception Loop: Updated Flow

```
Perception cycle detects events (existing)
    ↓
For each event:
    ↓
LLM Relevance Assessor (Haiku)
    ↓
┌─────────────────────────────────────┐
│ notification_tier = "push"           │
│                                      │
│ → Create Proactive Insight Surface   │
│ → Push to workspace via Redis/WS     │
│ → Wait for user engagement           │
│ → On engage: create plan, execute    │
│ → On dismiss: record, learn          │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ notification_tier = "briefing"       │
│                                      │
│ → Store as briefing_item memory      │
│ → Include relevance context          │
│ → Compiled into next briefing        │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ notification_tier = "silent"         │
│                                      │
│ → Update world model (entities)      │
│ → Store in memory if significant     │
│ → No user notification               │
└─────────────────────────────────────┘

Separately (periodic):
    ↓
Persona batch learning (every 10 interactions or daily)
Cross-source synthesis (when 2+ sources have signals)
```

### Autonomous Proactive Actions (High Trust Only)

At `autonomous` trust level, certain low-risk proactive actions can happen without the proposal step:

**Allowed without proposal:**
- Auto-filing emails into categories (internal operation)
- Updating briefing content (memory write, no external effect)
- Background memory and entity updates (silent tier)
- Scheduling perception cycles (internal operation)

**NEVER allowed without proposal:**
- Any external write (email, Slack, GitHub, calendar)
- Any action that the user would notice or that affects others
- Actions involving financial, legal, or sensitive content

This is enforced by the trust engine (Spec 2): external writes always go through the approval gate, regardless of trust level. The proposal step IS the approval step for proactive actions — the user accepting a suggestion is the approval.

## Files Changed

### New Files
- `src/services/relevance_assessor.py` — LLM relevance assessment with engagement history
- `src/models/engagement_history.py` — Engagement tracking model
- `frontend/src/components/a2ui/components/insight-surface.tsx` — Proactive insight surface
- Alembic migration for `engagement_history` table

### Modified Files
- `src/orchestrator/jarvis.py` — Remove per-message Persona call. Add Persona batch trigger logic.
- `src/services/scheduler.py` — Integrate relevance assessor into perception cycles. Update cross-source synthesis. Add Persona batch scheduling.
- `src/services/memory_service.py` — Enhanced briefing item storage with relevance metadata
- `src/services/surface_builder.py` — Include proactive insight surfaces in workspace build
- `src/orchestrator/perception.py` — Route assessed signals to appropriate tiers
- `frontend/src/components/a2ui/renderer.tsx` — Add InsightSurface to renderer
- `frontend/src/stores/surface-store.ts` — Handle insight → execution surface transitions

### Deleted
- Per-message Persona call in `process_message()` and `process_message_stream()`
- Fixed 30-minute cross-source synthesis cooldown

## Testing Strategy

- Unit tests for relevance assessment parsing (valid/invalid LLM outputs)
- Unit tests for notification tier routing (score × urgency → tier)
- Unit tests for engagement history update (engage, dismiss, ignore, suppression rules)
- Unit tests for Persona batch triggering (after N interactions, daily, after approval patterns)
- Integration test: perception signal → relevance assessment → insight surface pushed
- Integration test: user clicks suggested action → plan created → execution starts
- Integration test: user dismisses → engagement history updated → future signals deprioritized
- Integration test: 5 dismissals → auto-suppression → signals route to silent tier
- E2E test: email arrives → insight surface appears → user clicks "Draft reply" → execution surface shows progress → approval → email drafted

## Absorbed Issues from Audit

**Issue #18 — Notifier priority score computed but never used:** The Notifier already computes `priority_score = 0.30*urgency + 0.25*goal_relevance + 0.20*novelty + 0.15*confidence + 0.10*interruptibility` but discards it. This spec introduces relevance scoring for perception signals. Wire relevance assessment output into the existing priority score computation, then use the score to control delivery:

```python
# In notifier.py — use priority_score for delivery decisions
if priority_score < 0.3:
    # Silent — log only, don't deliver
    return
elif priority_score < 0.6:
    # Batch — add to briefing, don't push immediately
    await self._add_to_briefing(...)
    return
else:
    # Push — deliver to active surfaces immediately
    await self._deliver(...)
```

**Issue #5 — Notifier: No rate limiting per surface:** Proactive insight surfaces will push notifications. Without rate limiting, Spec 4 will spam users. Add per-surface rate caps:

```python
# In notifier.py
SURFACE_RATE_LIMITS = {
    "telegram": 5,   # max per hour
    "web": 15,       # max per hour (less intrusive)
    "slack": 8,      # max per hour
    "email": 3,      # max per hour (most intrusive)
}

async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
    key = f"notifier:rate:{user_id}:{surface}"
    count = await self._redis.incr(key)
    if count == 1:
        await self._redis.expire(key, 3600)  # 1 hour window
    limit = SURFACE_RATE_LIMITS.get(surface, 10)
    return count <= limit
```

When rate limit exceeded, notification is held for next briefing instead of dropped.

## Success Criteria

1. Detected signals are interpreted against user goals before any action
2. Only high-relevance signals push immediately; medium go to briefing; low are silent
3. Users see proactive insight surfaces with context (why this matters, what to do)
4. Clicking a suggestion seamlessly transitions to plan execution
5. Dismissals teach the system what the user doesn't care about
6. Persona costs reduced ~5x via batching with better signal quality
7. Cross-source synthesis identifies related signals across services
8. Notifier priority score drives delivery decisions (not ignored)
9. Per-surface rate limiting prevents notification spam

## Blast Radius

This spec primarily modifies the perception pipeline and notification system. It has the smallest blast radius of the four specs because most changes are additive (new assessor, new surface type, new model) rather than replacing existing contracts.

### Tier 1: CRITICAL — Core perception pipeline

| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/jarvis.py` | Remove 2 fire-and-forget Persona calls (lines ~813-826 in `process_message`, lines ~1160-1173 in `process_message_stream`). Add relevance assessment step in `run_perception_cycle()` between Librarian extraction and Planner evaluation. Update perception decision routing to push/briefing/silent tiers | Central orchestrator |
| `src/services/scheduler.py` | Remove `synthesis_cooldown = 1800` (line ~258). Replace fixed 30-min cooldown with signal-volume-based triggering in `_tick_perception()`. Add Persona batch trigger logic (every 10 interactions or daily) | Scheduler drives perception |

### Tier 2: HIGH — Signal routing & notification

| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/contracts.py` | Add `notification_tier` field to `PerceptionDecision` model. Add "proactive_insight" to `WorkspaceSurfacePush.kind` Literal | Contracts for tier routing and new surface type |
| `src/services/notifier.py` | Add handling for push-tier perception signals (new notification type distinct from `approval_request` and `info_update`) | Notification delivery |
| `src/services/memory_service.py` | Update `store_briefing_memory()` to accept `relevance_score` and `relevance_metadata` parameters | Briefing item storage with context |
| `src/services/surface_builder.py` | Add `_build_proactive_insight_surfaces()` method. Include insight surfaces in `build_workspace_surfaces()` | New surface type in workspace |
| `src/services/perception_policy.py` | May need adjustment for relevance-aware urgency calculations | Perception guardrails |

### Tier 3: MEDIUM — Models & rendering

| File | What changes | Why |
|------|-------------|-----|
| `src/models/briefings.py` | Add `relevance_score` (Float), `relevance_metadata` (JSONB) columns | Briefing item relevance context |
| `src/ui/renderer.py` | Add rendering rules for `proactive_insight` surface kind | Surface detail config |
| `src/orchestrator/prompts.py` | Update Planner prompt to receive relevance context from perception. Keep `PERSONA_PROMPT` (used by batch service now) | Prompt updates |
| `src/orchestrator/agents.py` | Persona agent definition unchanged — model tier and prompt stay the same. But calling pattern changes from per-message to batched | Agent config |
| `src/tools/intelligence_server.py` | Briefing generation may consider relevance metadata when building briefings | MCP tool |

### Tier 4: Frontend (Hard Replacement)

| File | What changes | Why |
|------|-------------|-----|
| `frontend/src/lib/types/surfaces.ts` | Add `"proactive_insight"` to SurfaceKind union type. | New surface kind |
| `frontend/src/lib/a2ui-types.ts` | Add `InsightSurfaceData` type: `{signal_source, signal_summary, relevance_reasoning, related_goals, suggested_actions: {description, capability}[], dismiss_available}`. Add `"insight"` to `JarvisMessage` type union. | Insight surface protocol |
| `frontend/src/components/workspace/surface-card.tsx` | Add rendering for `proactive_insight` kind — insight icon (💡), signal summary, related goal badges, suggested action buttons, dismiss button. Color: blue-violet (distinct from approval amber). | Surface card |
| `frontend/src/components/a2ui/components/insight-surface.tsx` | **NEW** — Full insight surface component. Shows signal context, relevance reasoning, suggested actions as clickable buttons, dismiss with optional feedback. On action click → transitions to execution surface (Spec 3). | Proactive insight display |
| `frontend/src/components/a2ui/renderer.tsx` | Add `insight_surface` case to component type switch. | Component registry |
| `frontend/src/app/page.tsx` | Sort proactive insights above static surfaces (same priority tier as active executions). Handle insight→execution surface transition when user clicks a suggested action. | Workspace layout |
| `frontend/src/stores/surface-store.ts` | Handle insight surface lifecycle: `proposal → accepted → executing → completed`. When user clicks action, update surface phase from proposal to executing (same surface ID becomes an execution surface from Spec 3). | Surface lifecycle |
| `frontend/src/lib/api.ts` | Add: `dismissInsight(surfaceId, reason?)` — records dismissal for engagement learning. No new fetch endpoints needed — insights arrive via WebSocket push. | API client |

### API Contract Changes

| Endpoint | What changes | Why |
|----------|-------------|-----|
| `WS /ws/{user_id}` | New message subtype: `{type: "surface", surface: {kind: "proactive_insight", ...}}` — uses existing surface push mechanism. | Insight delivery |
| `POST /v1/insights/{id}/dismiss` | **NEW** — Record dismissal for engagement learning. Body: `{reason?: string}`. | Dismissal feedback |
| `GET /v1/workspace/surfaces` | Includes proactive insight surfaces in workspace build. Insights sorted by relevance_score. | Workspace reconnection |

### Tier 5: Tests

| File | What changes | Why |
|------|-------------|-----|
| `tests/test_perception.py` | Add relevance assessor stage tests, tier routing tests | Perception pipeline |
| `tests/test_perception_execution.py` | Update perception pipeline verification | Pipeline flow |
| `tests/test_scheduler.py` | Remove 30-min cooldown tests, add signal-volume trigger tests, add Persona batch tests | Scheduler timing |
| `tests/test_orchestrator.py` | Remove per-message Persona call assertions | Persona removal |
| `tests/test_persona_golden.py` | May need refactoring for batch-mode testing | Persona behavior |
| `tests/test_briefing.py` | Add relevance metadata storage tests | Briefing items |
| `tests/test_briefing_read_model.py` | Add relevance metadata display tests | Briefing read |
| `tests/test_notifier.py` | Add push-tier signal routing tests | Notification |
| `tests/test_surface_registry.py` | Add proactive_insight surface kind tests | Surface registry |

### Tier 6: New files & migrations

| File | Type | Why |
|------|------|-----|
| `src/services/relevance_assessor.py` | NEW | LLM relevance assessment service |
| `src/models/engagement_history.py` | NEW | Engagement tracking model |
| `frontend/src/components/a2ui/components/insight-surface.tsx` | NEW | Proactive insight surface component |
| Alembic migration for `engagement_history` table | NEW | New table |
| Alembic migration for briefing relevance columns | NEW | Schema update |

### Key Risk: Persona Removal Timing

The Persona agent is called in **both** `process_message()` and `process_message_stream()`. Both calls must be removed simultaneously. If one path still calls Persona per-message while the other uses batching, the behavior diverges between API and streaming paths.

**Safety net:** Search for `"persona"` as agent name string in jarvis.py to find all call sites.

### Total: ~38 files affected (12 source, 9 tests, 8 frontend, 2 new models, 2 new migrations, 2 new services, 1 new API endpoint, 2 new components)
