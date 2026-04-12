"""Tests for proactive insight surface creation and push."""

from datetime import datetime, timezone


def test_insight_surface_data_model():
    """InsightSurfaceData validates correctly."""
    from src.orchestrator.contracts import InsightSurfaceData

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
    from src.orchestrator.contracts import InsightSurfaceData, SuggestedActionRef

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
    from src.orchestrator.contracts import WorkspaceSurfacePush

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
