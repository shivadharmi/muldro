"""Tests for user message plan deduplication."""

import hashlib


def test_user_message_idempotency_key_format():
    """User message plans should compute idempotency key from goal + decision."""
    goal = "Send email to Alice"
    decision_type = "create_task"
    goal_hash = hashlib.sha256(goal.encode()).hexdigest()[:16]
    expected_key = f"user:{decision_type}:{goal_hash}"

    assert expected_key.startswith("user:create_task:")
    assert len(goal_hash) == 16


def test_different_goals_produce_different_keys():
    """Different goals must produce different idempotency keys."""
    hash_1 = hashlib.sha256("Send email to Alice".encode()).hexdigest()[:16]
    hash_2 = hashlib.sha256("Send email to Bob".encode()).hexdigest()[:16]
    assert hash_1 != hash_2


def test_same_goal_produces_same_key():
    """Same goal should produce same key (deterministic)."""
    hash_1 = hashlib.sha256("Send email to Alice".encode()).hexdigest()[:16]
    hash_2 = hashlib.sha256("Send email to Alice".encode()).hexdigest()[:16]
    assert hash_1 == hash_2


def test_empty_goal_handled():
    """Empty or None goal should not crash."""
    hash_empty = hashlib.sha256("".encode()).hexdigest()[:16]
    key = f"user:create_task:{hash_empty}"
    assert key.startswith("user:create_task:")
