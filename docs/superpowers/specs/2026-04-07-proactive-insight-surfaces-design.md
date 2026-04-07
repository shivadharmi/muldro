# Spec 4B: Proactive Insight Surfaces

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 4A (Perception Signal Routing) — needs relevance assessor + tier routing; Spec 3 (Live Surfaces) — insight→execution surface transition reuses execution surface lifecycle
**Builds toward:** Complete proactive intelligence loop

## Problem Statement

Spec 4A built the perception signal routing backend (relevance assessment, notification tiers, rate limiting). This spec builds the **user-facing layer** — proactive insight surfaces that show what Jarvis noticed with suggested actions, the proposal→execution bridge, dismissal learning, and the frontend components.

## Design

### Component 1: Insight Surface Type

New surface kind `proactive_insight` in the A2UI system:

```python
# In contracts.py — add to WorkspaceSurfacePush.kind Literal
"proactive_insight"

# Insight surface data structure
class InsightSurfaceData(BaseModel):
    signal_source: str           # "gmail", "github", "calendar"
    signal_summary: str          # "Sarah Chen replied about Series A"
    relevance_reasoning: str     # "She's asking about Q2 revenue..."
    related_goals: list[str]     # Goal titles
    suggested_actions: list[SuggestedAction]  # From relevance assessor
    dismiss_available: bool = True
```

### Component 2: Insight Surface Push

When Spec 4A routes a signal to the `push` tier, create and push an insight surface:

```python
async def _push_insight_surface(self, signal, assessment, user_id, workspace_id):
    surface_id = f"surf_{ULID()}"
    surface = WorkspaceSurfacePush(
        id=surface_id,
        kind="proactive_insight",
        preview=SurfacePreview(
            title=signal.summary,
            subtitle=assessment.reasoning,
            status="proposal",
            priority="high" if assessment.urgency == "immediate" else "medium",
            tags=[signal.source],
        ),
        detail_config=None,  # Insight surfaces are self-contained (no tabs)
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    # Push via Redis → WebSocket
    await self._publish_surface(user_id, surface)
    # Persist to ui_surfaces
    await self._persist_surface(surface, user_id, workspace_id)
```

### Component 3: Proposal → Execution Bridge

When user clicks a suggested action on an insight surface, the surface transitions from `proposal` to execution (reusing Spec 3's execution surface lifecycle):

```python
# In routes_ws.py or a new action handler
async def handle_insight_action(surface_id, action_index, user_id, workspace_id):
    # Fetch the insight surface
    surface = await get_surface(surface_id)
    suggested = surface.data.suggested_actions[action_index]

    # Create a PlanOutput from the suggested action
    plan = PlanOutput(
        goal=suggested.description,
        steps=[PlanStep(
            step_id="step_1",
            description=suggested.description,
            capability=suggested.capability,
            input=suggested.action_input,
        )],
        achievable="full",
    )

    # Persist plan, create execution surface (reuse same surface_id)
    # The insight surface BECOMES an execution surface — same card, new phase
    await orchestrator.execute_plan(plan, user_id, workspace_id, surface_id=surface_id)
```

### Component 4: Dismissal Learning

New model: `src/models/engagement_history.py`

```python
class EngagementHistory(Base):
    __tablename__ = "engagement_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    signal_source: Mapped[str] = mapped_column(String, nullable=False)
    signal_category: Mapped[str] = mapped_column(String, nullable=False)
    engaged_count: Mapped[int] = mapped_column(default=0)
    dismissed_count: Mapped[int] = mapped_column(default=0)
    ignored_count: Mapped[int] = mapped_column(default=0)
    engagement_rate: Mapped[float] = mapped_column(default=0.5)
    last_engaged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_dismissed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    suppressed: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (UniqueConstraint("workspace_id", "signal_source", "signal_category"),)
```

**Suppression rules:**
- 3+ consecutive dismissals → lower relevance by 0.2
- 5+ consecutive dismissals → auto-suppress (stop surfacing)
- Any engagement on suppressed type → remove suppression

**Engagement history fed to relevance assessor** as context for future signals.

### Component 5: Dismiss API

```python
# New endpoint
@router.post("/v1/insights/{surface_id}/dismiss")
async def dismiss_insight(surface_id: str, body: DismissRequest = None):
    """Record dismissal for engagement learning."""
    await update_engagement_history(
        workspace_id=workspace_id,
        signal_source=surface.signal_source,
        signal_category=surface.signal_category,
        action="dismissed",
        reason=body.reason if body else None,
    )
    # Remove surface from workspace
    await remove_surface(surface_id)
    return {"status": "dismissed"}
```

### Component 6: Frontend Insight Components

**New component: `insight-surface.tsx`**
- Signal icon + source badge (Gmail, GitHub, etc.)
- Signal summary headline
- Relevance reasoning in muted text
- Related goal badges
- Suggested action buttons
- Dismiss button (with optional reason)
- On action click → calls WebSocket action → transitions to execution surface

**Surface card rendering for `proactive_insight` kind:**
- Color: blue-violet (distinct from approval amber)
- Status dot: pulsing blue (proposal phase)
- Priority sorting: above completed surfaces, alongside active executions

**Surface store lifecycle:**
- `proposal` → user clicks action → `accepted` → transitions to execution surface (Spec 3)
- `proposal` → user clicks dismiss → removed from store

## Files Changed

### New Files
- `src/models/engagement_history.py` — Engagement tracking model
- `src/api/routes_insights.py` — Dismiss endpoint
- `frontend/src/components/a2ui/components/insight-surface.tsx` — Insight component
- Alembic migration for `engagement_history` table
- `tests/test_engagement_history.py`
- `tests/test_insight_surfaces.py`

### Modified Files — Backend
- `src/orchestrator/jarvis.py` — Add `_push_insight_surface()` method, wire into push-tier signal handling
- `src/orchestrator/contracts.py` — Add `proactive_insight` to surface kind, add `InsightSurfaceData`
- `src/services/surface_builder.py` — Include insight surfaces in workspace build
- `src/services/relevance_assessor.py` — Accept engagement history as context input

### Modified Files — Frontend
- `frontend/src/lib/types/surfaces.ts` — Add `proactive_insight` to SurfaceKind
- `frontend/src/lib/a2ui-types.ts` — Add `InsightSurfaceData` type
- `frontend/src/lib/api.ts` — Add `dismissInsight()` API call
- `frontend/src/components/a2ui/renderer.tsx` — Add insight_surface case
- `frontend/src/components/workspace/surface-card.tsx` — Insight kind rendering (color, icon)
- `frontend/src/stores/surface-store.ts` — Insight→execution surface transition
- `frontend/src/app/page.tsx` — Sort insights alongside active executions

## Testing Strategy

- Unit tests: engagement history update (engage, dismiss, ignore, suppression rules)
- Unit tests: insight surface creation from push-tier signal
- Unit tests: proposal→execution bridge creates valid PlanOutput
- Integration: perception signal → push tier → insight surface created → user dismisses → engagement updated
- Integration: 5 dismissals → auto-suppression → future signals go to silent tier
- Frontend: insight component renders with actions, dismiss works

## Success Criteria

1. Push-tier signals create proactive insight surfaces in workspace
2. Clicking suggested action transitions insight → execution surface seamlessly
3. Dismissals update engagement history and influence future relevance
4. 5+ dismissals auto-suppress signal type
5. Frontend insight component renders correctly with actions and dismiss
6. Insight surfaces sorted prominently in workspace

## Blast Radius

**Moderate — mostly new files + additive surface type.**

| File | Change | Risk |
|------|--------|------|
| `src/orchestrator/jarvis.py` | Add `_push_insight_surface()` | **MEDIUM** — new method, not modifying existing |
| `src/orchestrator/contracts.py` | Add surface kind + data type | **LOW** — additive |
| Frontend components (7 files) | New component + additive rendering | **LOW** — additive |

### Total: ~20 files (5 backend modified, 6 new files, 7 frontend modified, 2 new tests)
