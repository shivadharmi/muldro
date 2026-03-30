"""Tests for Phase 11: Feature Flag + Registry-Driven Dispatch.

Covers: unified dispatch, can_use_tool_unified, is_auto_execute_tool,
flag gating, and composite/internal/external backend routing.
"""

from tests.conftest import make_mock_settings


class TestFeatureFlag:
    def test_flag_defaults_to_false(self):
        """JARVIS_USE_UNIFIED_DISPATCH defaults to False."""
        settings = make_mock_settings()
        assert settings.use_unified_dispatch is False

    def test_flag_can_be_enabled(self):
        """Flag can be set to True via make_mock_settings override."""
        settings = make_mock_settings(use_unified_dispatch=True)
        assert settings.use_unified_dispatch is True
