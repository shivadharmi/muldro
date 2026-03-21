"""Tests for BriefingReadModel — action constants and basic validation."""

from src.services.briefing_read_model import BriefingReadModel


class TestBriefingReadModelStatic:
    def test_class_exists(self):
        """Verify BriefingReadModel can be imported."""
        assert BriefingReadModel is not None

    def test_action_labels(self):
        """Verify lifecycle actions are well-formed."""
        from unittest.mock import MagicMock

        model = BriefingReadModel.__new__(BriefingReadModel)

        # Create a mock briefing
        briefing = MagicMock()
        briefing.status = "active"

        actions = model._get_actions(briefing)
        action_names = {a["action"] for a in actions}
        assert "pin" in action_names
        assert "snooze" in action_names
        assert "archive" in action_names

    def test_pinned_briefing_actions(self):
        """Pinned briefings should not offer 'pin' action."""
        from unittest.mock import MagicMock

        model = BriefingReadModel.__new__(BriefingReadModel)
        briefing = MagicMock()
        briefing.status = "pinned"

        actions = model._get_actions(briefing)
        action_names = {a["action"] for a in actions}
        assert "pin" not in action_names
        assert "snooze" in action_names
        assert "archive" in action_names
