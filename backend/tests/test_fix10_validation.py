"""Tests for Fix-10 Pydantic constraints and validators."""

import pytest
from pydantic import ValidationError

from src.api.routes_trust import TimePolicyRule
from src.api.schemas import BriefingFeedbackRequest, ScheduleCreateRequest, ScheduleUpdateRequest
from src.orchestrator.contracts import PerceptionDecision
from src.ui.contracts import DetailConfig, DetailTab

# ── BriefingFeedbackRequest ──────────────────────────────────────


class TestBriefingFeedbackRequest:
    def test_valid_feedback_type(self):
        r = BriefingFeedbackRequest(feedback_type="rating", rating=5)
        assert r.feedback_type == "rating"

    def test_invalid_feedback_type(self):
        with pytest.raises(ValidationError):
            BriefingFeedbackRequest(feedback_type="invalid")

    def test_rating_in_range(self):
        r = BriefingFeedbackRequest(feedback_type="rating", rating=1)
        assert r.rating == 1

    def test_rating_too_low(self):
        with pytest.raises(ValidationError):
            BriefingFeedbackRequest(feedback_type="rating", rating=0)

    def test_rating_too_high(self):
        with pytest.raises(ValidationError):
            BriefingFeedbackRequest(feedback_type="rating", rating=6)


# ── ScheduleCreateRequest ───────────────────────────────────────


class TestScheduleCreateRequest:
    def test_valid_schedule_type(self):
        r = ScheduleCreateRequest(name="t", action_type="x", schedule_type="one_shot")
        assert r.schedule_type == "one_shot"

    def test_invalid_schedule_type(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(name="t", action_type="x", schedule_type="bad")

    def test_invalid_priority(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(name="t", action_type="x", priority="urgent")

    def test_invalid_source(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(name="t", action_type="x", source="external")


class TestScheduleUpdateRequest:
    def test_invalid_priority(self):
        with pytest.raises(ValidationError):
            ScheduleUpdateRequest(priority="urgent")


# ── TimePolicyRule ──────────────────────────────────────────────


class TestTimePolicyRule:
    def test_valid_hours(self):
        r = TimePolicyRule(start_hour=0, end_hour=23, max_level="trusted")
        assert r.start_hour == 0

    def test_hour_too_high(self):
        with pytest.raises(ValidationError):
            TimePolicyRule(start_hour=24, end_hour=0, max_level="trusted")

    def test_hour_negative(self):
        with pytest.raises(ValidationError):
            TimePolicyRule(start_hour=-1, end_hour=0, max_level="trusted")


# ── PerceptionDecision ──────────────────────────────────────────


class TestPerceptionDecision:
    def test_next_check_seconds_valid(self):
        d = PerceptionDecision(next_check_seconds=30)
        assert d.next_check_seconds == 30

    def test_next_check_seconds_too_low(self):
        with pytest.raises(ValidationError):
            PerceptionDecision(next_check_seconds=10)

    def test_next_check_seconds_none(self):
        d = PerceptionDecision()
        assert d.next_check_seconds is None


# ── DetailConfig ────────────────────────────────────────────────


class TestDetailConfig:
    def test_valid_default_tab(self):
        tabs = [DetailTab(id="t1", label="Tab 1", endpoint="/t1")]
        c = DetailConfig(tabs=tabs, default_tab="t1")
        assert c.default_tab == "t1"

    def test_invalid_default_tab(self):
        tabs = [DetailTab(id="t1", label="Tab 1", endpoint="/t1")]
        with pytest.raises(ValidationError, match="default_tab"):
            DetailConfig(tabs=tabs, default_tab="nonexistent")

    def test_none_default_tab(self):
        tabs = [DetailTab(id="t1", label="Tab 1", endpoint="/t1")]
        c = DetailConfig(tabs=tabs, default_tab=None)
        assert c.default_tab is None

    def test_empty_tabs_with_default_tab(self):
        # Empty tabs list with default_tab set should pass (tabs is empty, validator skips)
        c = DetailConfig(tabs=[], default_tab="anything")
        assert c.default_tab == "anything"
