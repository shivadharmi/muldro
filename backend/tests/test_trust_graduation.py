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

    # ── SVC-P1-1 boundary characterization ──────────────────────────
    def test_high_volume_moderate_rejection_stays_learning(self):
        """30 approved, 4 rejected = 11.8% (in the old 10-15% lenient band).

        High volume no longer rescues a frequently-rejected capability to
        trusted; it stays learning (gated).
        """
        state = _make_state(approved=30, rejected=4)
        assert graduate_trust(state) == "learning"

    def test_exact_ten_percent_rejection_stays_learning(self):
        """Strict trusted cap: exactly 10% rejection does NOT earn trusted."""
        state = _make_state(approved=18, rejected=2)  # 2/20 == 10%
        assert graduate_trust(state) == "learning"

    def test_just_under_ten_percent_is_trusted(self):
        """Just below the 10% cap earns trusted."""
        state = _make_state(approved=19, rejected=2)  # 2/21 ≈ 9.5%
        assert graduate_trust(state) == "trusted"

    def test_exact_five_percent_is_not_autonomous(self):
        """Strict autonomous cap: 5% rejection earns trusted, not autonomous."""
        state = _make_state(approved=38, rejected=2)  # 2/40 == 5.0%
        assert graduate_trust(state) == "trusted"


class TestGraduationConsistency:
    """The Trust-tab UI (_graduation_progress) and the gate (graduate_trust) must
    agree, since both now derive from GRADUATION_THRESHOLDS (anti-drift guard)."""

    def test_progress_blocked_iff_gate_withholds_promotion(self):
        from src.services.trust_engine import _graduation_progress

        # A 'learning' capability with 12% rejection: the gate keeps it at
        # learning, and the UI must show the next level (trusted) as blocked.
        state = _make_state(approved=20, rejected=3, trust_level="learning")  # 13%
        assert graduate_trust(state) == "learning"
        progress = _graduation_progress(state)
        assert progress["next_level"] == "trusted"
        assert progress["blocked_by_rejections"] is True

    def test_progress_not_blocked_when_gate_would_promote(self):
        from src.services.trust_engine import _graduation_progress

        # Clean enough to graduate learning -> trusted: UI must not show blocked.
        state = _make_state(approved=20, rejected=1, trust_level="learning")  # ~4.8%
        assert graduate_trust(state) == "trusted"
        progress = _graduation_progress(state)
        assert progress["blocked_by_rejections"] is False


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
