"""Tests for trust graduation rules — pure function, no DB."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.services.risk_assessor import apply_rejection, graduate_trust

TRUST_LEVELS = ("first_use", "learning", "trusted", "autonomous")


def _make_state(
    approved: int = 0,
    rejected: int = 0,
    modified: int = 0,
    trust_level: str = "first_use",
    cooldown_until: datetime | None = None,
) -> MagicMock:
    s = MagicMock()
    s.approved_count = approved
    s.rejected_count = rejected
    s.modified_count = modified
    s.trust_level = trust_level
    s.cooldown_until = cooldown_until
    return s


class TestGraduateTrust:
    def test_zero_decisions_stays_first_use(self):
        state = _make_state(approved=0, rejected=0)
        assert graduate_trust(state) == "first_use"

    def test_three_approvals_zero_rejections_graduates_to_learning(self):
        state = _make_state(approved=3, rejected=0)
        assert graduate_trust(state) == "learning"

    def test_two_approvals_stays_first_use(self):
        state = _make_state(approved=2, rejected=0)
        assert graduate_trust(state) == "first_use"

    def test_ten_approvals_low_rejection_graduates_to_trusted(self):
        state = _make_state(approved=10, rejected=1)
        assert graduate_trust(state) == "trusted"

    def test_ten_approvals_high_rejection_stays_learning(self):
        # 10 approved, 2 rejected = 2/12 ≈ 16.7% > 10%
        state = _make_state(approved=10, rejected=2)
        assert graduate_trust(state) == "learning"

    def test_twentyfive_approvals_graduates_to_autonomous(self):
        state = _make_state(approved=25, rejected=1)
        assert graduate_trust(state) == "autonomous"

    def test_twentyfive_approvals_high_rejection_stays_trusted(self):
        # 25 approved, 2 rejected = 2/27 ≈ 7.4% > 5%
        state = _make_state(approved=25, rejected=2)
        assert graduate_trust(state) == "trusted"

    def test_cooldown_blocks_graduation(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        state = _make_state(approved=30, rejected=0, trust_level="learning", cooldown_until=future)
        assert graduate_trust(state) == "learning"

    def test_expired_cooldown_allows_graduation(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        state = _make_state(approved=30, rejected=0, trust_level="learning", cooldown_until=past)
        assert graduate_trust(state) == "autonomous"

    def test_three_approvals_with_one_rejection_stays_first_use(self):
        state = _make_state(approved=3, rejected=1)
        assert graduate_trust(state) == "first_use"


class TestApplyRejection:
    def test_autonomous_demotes_to_trusted_72h(self):
        state = _make_state(approved=30, rejected=0, trust_level="autonomous")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "trusted"
        assert state.cooldown_until is not None
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 71 < cooldown_hours < 73

    def test_trusted_demotes_to_learning_48h(self):
        state = _make_state(approved=15, rejected=0, trust_level="trusted")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 47 < cooldown_hours < 49

    def test_learning_demotes_to_first_use_24h(self):
        state = _make_state(approved=5, rejected=0, trust_level="learning")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "first_use"
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 23 < cooldown_hours < 25

    def test_first_use_stays_first_use(self):
        state = _make_state(approved=1, rejected=0, trust_level="first_use")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "first_use"
        assert state.cooldown_until is None
