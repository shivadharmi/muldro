"""Tests for InitiativeScorer — proactive autonomy scoring."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.initiative_scorer import (
    DEFAULT_AUTO_PLAN_THRESHOLD,
    INITIATIVE_WEIGHTS,
    InitiativeResult,
    InitiativeScorer,
)


def _make_event(
    importance=0.5,
    urgency=0.3,
    title="Test Event",
    summary="Summary",
    source="gmail",
    event_type="email.received",
    importance_signals=None,
    actor_entities=None,
):
    event = MagicMock()
    event.event_id = "evt_test"
    event.importance_score = importance
    event.urgency_score = urgency
    event.title = title
    event.summary = summary
    event.source = source
    event.event_type = event_type
    event.importance_signals = importance_signals
    event.actor_entities = actor_entities
    return event


# ── Weight Config ─────────────────────────────────────────────


class TestWeights:
    def test_weights_sum_to_one(self):
        total = sum(INITIATIVE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_default_threshold_reasonable(self):
        assert 0.5 <= DEFAULT_AUTO_PLAN_THRESHOLD <= 0.9


# ── Basic Scoring ─────────────────────────────────────────────


class TestBasicScoring:
    @pytest.mark.asyncio
    async def test_low_importance_event_no_plan(self):
        """Low-importance events should not trigger auto-planning."""
        scorer = InitiativeScorer(db=MagicMock())
        event = _make_event(importance=0.2, urgency=0.1)
        result = await scorer.score(event, "usr_1")

        assert isinstance(result, InitiativeResult)
        assert result.should_plan is False
        assert result.score < DEFAULT_AUTO_PLAN_THRESHOLD

    @pytest.mark.asyncio
    async def test_high_importance_event_triggers_plan(self):
        """Very high importance + urgency + priority person should auto-plan."""
        scorer = InitiativeScorer(db=MagicMock())
        event = _make_event(
            importance=0.95,
            urgency=0.95,
            importance_signals={
                "from_priority_person": True,
                "contains_deadline": True,
            },
        )
        result = await scorer.score(event, "usr_1")

        assert result.should_plan is True
        assert result.score >= DEFAULT_AUTO_PLAN_THRESHOLD

    @pytest.mark.asyncio
    async def test_medium_event_notifies_but_no_plan(self):
        """Medium-importance events should notify but not auto-plan."""
        scorer = InitiativeScorer(db=MagicMock(), notify_threshold=0.30)
        event = _make_event(importance=0.6, urgency=0.4)
        result = await scorer.score(event, "usr_1")

        assert result.should_notify is True
        assert result.should_plan is False

    @pytest.mark.asyncio
    async def test_result_includes_signals(self):
        scorer = InitiativeScorer(db=MagicMock())
        event = _make_event(importance=0.7, urgency=0.5)
        result = await scorer.score(event, "usr_1")

        assert "importance" in result.signals
        assert "urgency" in result.signals
        assert "goal_relevance" in result.signals
        assert "entity_significance" in result.signals
        assert "novelty" in result.signals


# ── Priority Person Boost ─────────────────────────────────────


class TestBoosts:
    @pytest.mark.asyncio
    async def test_priority_person_boost(self):
        """Events from priority people should get a score boost."""
        scorer = InitiativeScorer(db=MagicMock())
        signals = {"from_priority_person": True}

        event_normal = _make_event(importance=0.6, urgency=0.5)
        event_priority = _make_event(importance=0.6, urgency=0.5, importance_signals=signals)

        result_normal = await scorer.score(event_normal, "usr_1")
        result_priority = await scorer.score(event_priority, "usr_1")

        assert result_priority.score > result_normal.score

    @pytest.mark.asyncio
    async def test_deadline_boost(self):
        """Events with deadlines should get a score boost."""
        scorer = InitiativeScorer(db=MagicMock())
        signals = {"contains_deadline": True}

        event_normal = _make_event(importance=0.6, urgency=0.5)
        event_deadline = _make_event(importance=0.6, urgency=0.5, importance_signals=signals)

        result_normal = await scorer.score(event_normal, "usr_1")
        result_deadline = await scorer.score(event_deadline, "usr_1")

        assert result_deadline.score > result_normal.score


# ── Goal Relevance ────────────────────────────────────────────


class TestGoalRelevance:
    @pytest.mark.asyncio
    async def test_goal_relevant_event_scores_higher(self):
        """Events related to active goals (via memory) should score higher."""
        memory_service = MagicMock()
        memory_service.retrieve = AsyncMock(
            return_value=[{"fact_text": "Goal: Launch product beta", "memory_type": "goal"}]
        )

        scorer = InitiativeScorer(db=MagicMock(), memory_service=memory_service)
        event = _make_event(
            importance=0.5,
            urgency=0.5,
            title="Product beta feedback from users",
        )
        result = await scorer.score(event, "usr_1")

        assert result.signals["goal_relevance"] > 0

    @pytest.mark.asyncio
    async def test_no_goals_returns_zero(self):
        """No active goals should return zero goal_relevance."""
        memory_service = MagicMock()
        memory_service.retrieve = AsyncMock(return_value=[])

        scorer = InitiativeScorer(db=MagicMock(), memory_service=memory_service)
        event = _make_event()
        result = await scorer.score(event, "usr_1")

        assert result.signals["goal_relevance"] == 0.0


# ── Entity Significance ──────────────────────────────────────


class TestEntitySignificance:
    @pytest.mark.asyncio
    async def test_known_entity_adds_significance(self):
        """Events from known high-importance entities should score higher."""
        world_model = MagicMock()
        world_model.find_entity = AsyncMock(
            return_value=[
                {
                    "canonical_name": "John Doe",
                    "entity_type": "person",
                    "importance_score": 0.9,
                }
            ]
        )

        scorer = InitiativeScorer(db=MagicMock(), world_model=world_model)
        event = _make_event(actor_entities=[{"name": "John Doe", "email": "john@example.com"}])
        result = await scorer.score(event, "usr_1")

        assert result.signals["entity_significance"] == 0.9

    @pytest.mark.asyncio
    async def test_unknown_entity_zero_significance(self):
        """Events without actor entities should have zero entity significance."""
        scorer = InitiativeScorer(db=MagicMock())
        event = _make_event(actor_entities=None)
        result = await scorer.score(event, "usr_1")

        assert result.signals["entity_significance"] == 0.0


# ── Novelty ───────────────────────────────────────────────────


class TestNovelty:
    @pytest.mark.asyncio
    async def test_novel_event_high_novelty(self):
        """Events with no related memories should have high novelty."""
        memory_service = MagicMock()
        memory_service.retrieve = AsyncMock(return_value=[])

        scorer = InitiativeScorer(db=MagicMock(), memory_service=memory_service)
        event = _make_event(title="Completely new topic")
        result = await scorer.score(event, "usr_1")

        assert result.signals["novelty"] == 0.9

    @pytest.mark.asyncio
    async def test_duplicate_event_low_novelty(self):
        """Events very similar to existing memories should have low novelty."""
        memory_service = MagicMock()
        memory_service.retrieve = AsyncMock(
            return_value=[{"fact_text": "Same old", "relevance_score": 0.95}]
        )

        scorer = InitiativeScorer(db=MagicMock(), memory_service=memory_service)
        event = _make_event(title="Same old")
        result = await scorer.score(event, "usr_1")

        assert result.signals["novelty"] < 0.2

    @pytest.mark.asyncio
    async def test_no_memory_service_default_novelty(self):
        """Without memory service, novelty defaults to 0.5."""
        scorer = InitiativeScorer(db=MagicMock())
        event = _make_event()
        result = await scorer.score(event, "usr_1")

        assert result.signals["novelty"] == 0.5


# ── Custom Thresholds ─────────────────────────────────────────


class TestCustomThresholds:
    @pytest.mark.asyncio
    async def test_custom_plan_threshold(self):
        """Custom auto_plan_threshold should be respected."""
        scorer = InitiativeScorer(db=MagicMock(), auto_plan_threshold=0.30)
        event = _make_event(importance=0.6, urgency=0.6)
        result = await scorer.score(event, "usr_1")

        assert result.should_plan is True

    @pytest.mark.asyncio
    async def test_custom_notify_threshold(self):
        """Custom notify_threshold should be respected."""
        scorer = InitiativeScorer(db=MagicMock(), notify_threshold=0.90)
        event = _make_event(importance=0.5, urgency=0.3)
        result = await scorer.score(event, "usr_1")

        assert result.should_notify is False
