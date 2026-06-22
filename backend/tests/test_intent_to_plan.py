"""Tests for intent_to_plan() and expanded FAST_INTENTS."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.contracts import PlanOutput
from src.orchestrator.intent_classifier import (
    _VALID_INTENTS,
    FAST_INTENTS,
    INTENT_CLASSIFIER_PROMPT,
    classify_intent,
    intent_to_plan,
)


def _mock_client_returning(text: str):
    """Build a mock Anthropic client whose response yields a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


class TestClassifyIntentParsing:
    """ORCH-P2-3: intent JSON parse must survive prose around the JSON object."""

    @pytest.mark.asyncio
    async def test_parses_json_wrapped_in_prose_and_fences(self):
        # Stray braces in prose + a fenced JSON block — the old index/rindex
        # brace matcher would slice from the first '{' to the last '}' and fail.
        text = (
            "Here is my analysis {note: braces in prose}.\n"
            '```json\n{"intent": "greeting", "confidence": 0.9, "sources": []}\n```'
        )
        client = _mock_client_returning(text)

        intent, confidence, sources = await classify_intent(
            client, "claude-haiku-4-5-20251001", "hi there"
        )

        assert intent == "greeting"
        assert confidence == 0.9
        assert sources == []

    @pytest.mark.asyncio
    async def test_non_json_response_falls_back_to_command(self):
        client = _mock_client_returning("I'm not sure how to classify this.")

        intent, confidence, sources = await classify_intent(
            client, "claude-haiku-4-5-20251001", "???"
        )

        assert intent == "command"
        assert sources == []


# ── Test expanded FAST_INTENTS ───────────────────────────────────────


class TestExpandedFastIntents:
    """Verify the 4 new fast intents are present in all relevant constants."""

    NEW_INTENTS = {"direct_answer", "single_read", "memory_operation", "acknowledgment"}
    ORIGINAL_INTENTS = {
        "greeting",
        "chitchat",
        "simple_question",
        "data_fetch",
        "status_query",
        "approval_response",
    }

    def test_fast_intents_contains_originals(self):
        for intent in self.ORIGINAL_INTENTS:
            assert intent in FAST_INTENTS, f"Missing original: {intent}"

    def test_fast_intents_contains_new(self):
        for intent in self.NEW_INTENTS:
            assert intent in FAST_INTENTS, f"Missing new intent: {intent}"

    def test_fast_intents_total_count(self):
        assert len(FAST_INTENTS) == 10

    def test_valid_intents_contains_new(self):
        for intent in self.NEW_INTENTS:
            assert intent in _VALID_INTENTS, f"Missing from _VALID_INTENTS: {intent}"

    def test_valid_intents_superset_of_fast(self):
        assert FAST_INTENTS.issubset(_VALID_INTENTS)

    def test_classifier_prompt_mentions_new_intents(self):
        for intent in self.NEW_INTENTS:
            assert intent in INTENT_CLASSIFIER_PROMPT, (
                f"INTENT_CLASSIFIER_PROMPT missing '{intent}'"
            )


# ── Test intent_to_plan ──────────────────────────────────────────────


class TestIntentToPlan:
    """Map fast intents to lightweight PlanOutput."""

    CAPS = [
        "email.search",
        "calendar.list",
        "messaging.get_history",
        "repo.list_prs",
        "knowledge.search",
    ]

    def test_greeting_returns_respond(self):
        result = intent_to_plan("greeting", "Hey Jarvis!", self.CAPS)
        assert isinstance(result, PlanOutput)
        assert result.priority == "low"
        assert len(result.steps) == 1
        assert result.steps[0].capability == "respond"

    def test_chitchat_returns_respond(self):
        result = intent_to_plan("chitchat", "How are you?", self.CAPS)
        assert result.priority == "low"
        assert result.steps[0].capability == "respond"

    def test_acknowledgment_returns_respond(self):
        result = intent_to_plan("acknowledgment", "Ok got it, thanks", self.CAPS)
        assert result.priority == "low"
        assert result.steps[0].capability == "respond"

    def test_direct_answer_returns_reason(self):
        result = intent_to_plan("direct_answer", "What's 2+2?", self.CAPS)
        assert result.steps[0].capability == "reason"
        assert result.priority == "medium"

    def test_simple_question_returns_reason(self):
        result = intent_to_plan("simple_question", "What's John's email?", self.CAPS)
        assert result.steps[0].capability == "reason"

    def test_single_read_uses_perceive(self):
        result = intent_to_plan("single_read", "Check my email", self.CAPS)
        assert result.steps[0].capability == "perceive"

    def test_single_read_calendar_uses_perceive(self):
        result = intent_to_plan("single_read", "Show my schedule", self.CAPS)
        assert result.steps[0].capability == "perceive"

    def test_data_fetch_uses_perceive(self):
        result = intent_to_plan("data_fetch", "Check Slack messages", self.CAPS)
        assert result.steps[0].capability == "perceive"

    def test_data_fetch_email_uses_perceive(self):
        result = intent_to_plan("data_fetch", "Any new emails?", self.CAPS)
        assert result.steps[0].capability == "perceive"

    def test_status_query_returns_knowledge_search(self):
        result = intent_to_plan("status_query", "What are my goals?", self.CAPS)
        assert result.steps[0].capability == "knowledge.search"

    def test_memory_operation_returns_knowledge_search(self):
        result = intent_to_plan(
            "memory_operation", "Remember John prefers morning calls", self.CAPS
        )
        assert result.steps[0].capability == "knowledge.search"

    def test_approval_response_returns_respond(self):
        result = intent_to_plan("approval_response", "Yes, approve that", self.CAPS)
        assert result.steps[0].capability == "respond"

    def test_unknown_intent_returns_respond_fallback(self):
        result = intent_to_plan("unknown_intent", "???", self.CAPS)
        assert result.steps[0].capability == "respond"
        assert result.priority == "low"

    def test_goal_preserves_full_message(self):
        long_msg = "x" * 500
        result = intent_to_plan("greeting", long_msg, self.CAPS)
        assert result.goal == long_msg

    def test_all_fast_intents_handled(self):
        """Every intent in FAST_INTENTS produces a valid PlanOutput."""
        for intent in FAST_INTENTS:
            result = intent_to_plan(intent, "test message", self.CAPS)
            assert isinstance(result, PlanOutput), f"Failed for intent: {intent}"
            assert len(result.steps) >= 1, f"No steps for intent: {intent}"

    def test_all_fast_intents_have_step_ids(self):
        """Every step from intent_to_plan must have a non-empty step_id."""
        for intent in FAST_INTENTS:
            result = intent_to_plan(intent, "test message", self.CAPS)
            for i, step in enumerate(result.steps):
                assert step.step_id, f"Empty step_id for intent={intent}, step={i}"

    def test_step_ids_are_sequential(self):
        """Step IDs follow s1, s2, ... pattern."""
        result = intent_to_plan("greeting", "Hey!", self.CAPS)
        assert result.steps[0].step_id == "s1"

    def test_single_read_risk_is_none(self):
        result = intent_to_plan("single_read", "Check email", self.CAPS)
        assert result.steps[0].risk == "none"

    def test_data_fetch_risk_is_none(self):
        result = intent_to_plan("data_fetch", "Show calendar", self.CAPS)
        assert result.steps[0].risk == "none"


# ── Test old functions removed ─────────────────────────────────────


class TestOldFunctionsRemoved:
    """intent_to_decision and extract_decision are deleted."""

    def test_intent_to_decision_not_available(self):
        from src.orchestrator import intent_classifier

        assert not hasattr(intent_classifier, "intent_to_decision")

    def test_extract_decision_not_available(self):
        from src.orchestrator import intent_classifier

        assert not hasattr(intent_classifier, "extract_decision")
