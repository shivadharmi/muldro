"""Tests for intent_to_plan(), _match_read_capability(), and expanded FAST_INTENTS."""

from __future__ import annotations

from src.orchestrator.contracts import PlanOutput
from src.orchestrator.intent_classifier import (
    _VALID_INTENTS,
    FAST_INTENTS,
    INTENT_CLASSIFIER_PROMPT,
    _match_read_capability,
    intent_to_plan,
)

# ── Test _match_read_capability ──────────────────────────────────────


class TestMatchReadCapability:
    """Keyword-based capability matching for fast-path single-read intents."""

    CAPS = [
        "email.search",
        "email.read",
        "calendar.list",
        "calendar.get",
        "messaging.get_history",
        "messaging.search",
        "repo.list_prs",
        "repo.search_code",
        "knowledge.search",
    ]

    def test_email_keywords(self):
        assert _match_read_capability("Check my email", self.CAPS) == "email.search"

    def test_mail_keyword(self):
        assert _match_read_capability("Any new mail?", self.CAPS) == "email.search"

    def test_inbox_keyword(self):
        assert _match_read_capability("Show my inbox", self.CAPS) == "email.search"

    def test_gmail_keyword(self):
        assert _match_read_capability("Open gmail", self.CAPS) == "email.search"

    def test_calendar_keywords(self):
        result = _match_read_capability("What's on my calendar today?", self.CAPS)
        assert result.startswith("calendar.")

    def test_schedule_keyword(self):
        result = _match_read_capability("Show my schedule", self.CAPS)
        assert result.startswith("calendar.")

    def test_meeting_keyword(self):
        result = _match_read_capability("Any meetings tomorrow?", self.CAPS)
        assert result.startswith("calendar.")

    def test_slack_keywords(self):
        result = _match_read_capability("Check Slack messages", self.CAPS)
        assert result.startswith("messaging.")

    def test_channel_keyword(self):
        result = _match_read_capability("What's new in the channel?", self.CAPS)
        assert result.startswith("messaging.")

    def test_github_keywords(self):
        result = _match_read_capability("Any new PRs on GitHub?", self.CAPS)
        assert result.startswith("repo.")

    def test_issue_keyword(self):
        result = _match_read_capability("Show open issues", self.CAPS)
        assert result.startswith("repo.")

    def test_fallback_to_knowledge_search(self):
        result = _match_read_capability("What's the weather?", self.CAPS)
        assert result == "knowledge.search"

    def test_empty_message_falls_back(self):
        assert _match_read_capability("", self.CAPS) == "knowledge.search"

    def test_exact_cap_preferred_over_family(self):
        caps_with_exact = ["email.search", "email.list"]
        assert _match_read_capability("Check email", caps_with_exact) == "email.search"

    def test_empty_capabilities_returns_default(self):
        result = _match_read_capability("Check my email", [])
        assert result == "email.search"

    def test_case_insensitive(self):
        assert _match_read_capability("CHECK MY EMAIL", self.CAPS) == "email.search"


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

    def test_single_read_uses_keyword_match(self):
        result = intent_to_plan("single_read", "Check my email", self.CAPS)
        assert result.steps[0].capability == "email.search"

    def test_single_read_calendar(self):
        result = intent_to_plan("single_read", "Show my schedule", self.CAPS)
        assert result.steps[0].capability.startswith("calendar.")

    def test_data_fetch_uses_keyword_match(self):
        result = intent_to_plan("data_fetch", "Check Slack messages", self.CAPS)
        assert result.steps[0].capability.startswith("messaging.")

    def test_data_fetch_email(self):
        result = intent_to_plan("data_fetch", "Any new emails?", self.CAPS)
        assert result.steps[0].capability == "email.search"

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

    def test_goal_truncated_to_200_chars(self):
        long_msg = "x" * 500
        result = intent_to_plan("greeting", long_msg, self.CAPS)
        assert len(result.goal) <= 200

    def test_all_fast_intents_handled(self):
        """Every intent in FAST_INTENTS produces a valid PlanOutput."""
        for intent in FAST_INTENTS:
            result = intent_to_plan(intent, "test message", self.CAPS)
            assert isinstance(result, PlanOutput), f"Failed for intent: {intent}"
            assert len(result.steps) >= 1, f"No steps for intent: {intent}"

    def test_single_read_risk_is_none(self):
        result = intent_to_plan("single_read", "Check email", self.CAPS)
        assert result.steps[0].risk == "none"

    def test_data_fetch_risk_is_none(self):
        result = intent_to_plan("data_fetch", "Show calendar", self.CAPS)
        assert result.steps[0].risk == "none"
