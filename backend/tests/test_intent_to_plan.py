"""Tests for intent_to_plan(), _match_read_capability(), and expanded FAST_INTENTS."""

from __future__ import annotations

from src.orchestrator.intent_classifier import (
    _VALID_INTENTS,
    FAST_INTENTS,
    INTENT_CLASSIFIER_PROMPT,
    _match_read_capability,
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
