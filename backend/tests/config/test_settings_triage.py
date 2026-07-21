"""Tests for triage-related Settings flags."""

from src.config.settings import Settings


def test_triage_flags_default_off():
    """Both perception triage flags default to False."""
    s = Settings()
    assert s.perception_triage_enabled is False
    assert s.perception_triage_shadow is False
