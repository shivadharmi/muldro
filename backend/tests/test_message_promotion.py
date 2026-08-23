"""Tests for the message-promotion gate."""

from src.services.message_promotion import should_promote_to_workspace


def test_nothing_promotes_by_default():
    """The gate is closed unless the turn says otherwise. Its old open path was a
    walk over a model-authored component tree, which was the model rating its own
    output one step removed — that is gone, so the default is now no promotion."""
    assert should_promote_to_workspace() is False


def test_explicit_flag_promotes():
    assert should_promote_to_workspace(explicit_flag=True) is True
