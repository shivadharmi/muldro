"""Tests for proactive insight surface creation and push."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


def test_insight_surface_data_model():
    """InsightSurfaceData validates correctly."""
    from src.contracts import InsightSurfaceData

    data = InsightSurfaceData(
        signal_source="gmail",
        signal_category="reply",
        signal_summary="Sarah replied about Series A",
        relevance_score=0.85,
        relevance_reasoning="Relates to active fundraising goal",
        related_goals=["Close Series A"],
        suggested_actions=[],
    )
    assert data.signal_source == "gmail"
    assert data.dismiss_available is True


def test_insight_surface_data_with_actions():
    """InsightSurfaceData with suggested actions."""
    from src.contracts import InsightSurfaceData, SuggestedActionRef

    action = SuggestedActionRef(
        description="Draft reply to Sarah",
        capability="email.draft",
        action_input={"to": "sarah@example.com"},
    )
    data = InsightSurfaceData(
        signal_source="gmail",
        signal_summary="Sarah replied",
        suggested_actions=[action],
    )
    assert len(data.suggested_actions) == 1
    assert data.suggested_actions[0].capability == "email.draft"


def test_workspace_surface_push_accepts_proactive_insight():
    """WorkspaceSurfacePush accepts kind='proactive_insight'."""
    from src.contracts import WorkspaceSurfacePush

    surface = WorkspaceSurfacePush(
        id="surf_test123",
        kind="proactive_insight",
        preview={
            "title": "Test",
            "subtitle": None,
            "status": "proposal",
            "priority": "high",
            "metrics": [],
            "entities": [],
            "progress": None,
            "timestamp": None,
            "tags": ["gmail"],
        },
        detail_config=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert surface.kind == "proactive_insight"


def test_insight_surface_data_carries_evidence():
    """InsightSurfaceData accepts a human-readable evidence string (defaults None)."""
    from src.contracts import InsightSurfaceData

    assert InsightSurfaceData(signal_source="gmail", signal_summary="x").evidence is None
    data = InsightSurfaceData(
        signal_source="gmail",
        signal_summary="Recurring vendor charge",
        evidence="4 recurrences",
    )
    assert data.evidence == "4 recurrences"


def test_format_evidence_phrasing():
    """format_evidence turns a count + unit into a readable string, None when empty."""
    from src.services.relevance_assessor import format_evidence

    assert format_evidence(4, "recurrences") == "4 recurrences"
    assert format_evidence(42, "days observed") == "42 days observed"
    assert format_evidence(3, None) == "3 observed"
    assert format_evidence(0, "recurrences") is None
    assert format_evidence(None, "recurrences") is None


def _make_pusher_capturing_publishes():
    """Build a SurfacePusher whose event bus captures published WS messages and
    whose db_factory yields a no-op async session (persistence is exercised)."""
    from src.orchestrator.surface_pusher import SurfacePusher

    published: list[str] = []

    event_bus = MagicMock()
    event_bus.publish_to_channel = AsyncMock(side_effect=lambda channel, msg: published.append(msg))
    event_bus._redis = None  # disables rate-limiting → push allowed

    events = MagicMock()
    events.ensure_event_bus = AsyncMock(return_value=event_bus)

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=cm)

    pusher = SurfacePusher(events=events, db_factory_provider=lambda: db_factory)
    return pusher, published


async def test_push_insight_surface_wires_evidence_to_data_and_preview():
    """evidence_count/unit on the assessment flows into InsightSurfaceData.evidence
    and onto the pushed SurfacePreview.evidence."""
    from src.services.relevance_assessor import PerceptionSignal, RelevanceAssessment

    pusher, published = _make_pusher_capturing_publishes()

    signal = PerceptionSignal(
        source="gmail",
        event_type="email_received",
        summary="- [gmail] email_received: Recurring vendor charge (event_id=evt_01)",
    )
    assessment = RelevanceAssessment(
        relevance_score=0.9,
        reasoning="You have been charged by this vendor repeatedly",
        urgency="today",
        evidence_count=4,
        evidence_unit="recurrences",
    )

    await pusher.push_insight_surface(
        signal=signal,
        assessment=assessment,
        user_id="usr_test",
        workspace_id="ws_test",
    )

    assert len(published) == 1
    payload = json.loads(published[0])["surface"]
    assert payload["insight_data"]["evidence"] == "4 recurrences"
    assert payload["preview"]["evidence"] == "4 recurrences"


async def test_push_insight_surface_omits_evidence_when_absent():
    """No evidence_count → evidence is None on both data and preview."""
    from src.services.relevance_assessor import PerceptionSignal, RelevanceAssessment

    pusher, published = _make_pusher_capturing_publishes()

    await pusher.push_insight_surface(
        signal=PerceptionSignal(source="gmail", event_type="x", summary="Hello"),
        assessment=RelevanceAssessment(relevance_score=0.5, reasoning="meh"),
        user_id="usr_test",
        workspace_id="ws_test",
    )

    assert len(published) == 1
    payload = json.loads(published[0])["surface"]
    assert payload["insight_data"]["evidence"] is None
    assert payload["preview"]["evidence"] is None
