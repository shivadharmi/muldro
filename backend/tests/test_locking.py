"""Tests for execution locking utilities."""

from src.services.locking import _resource_to_lock_id


def test_resource_to_lock_id_deterministic():
    """Same key should produce the same lock ID."""
    id1 = _resource_to_lock_id("execution:exec_001")
    id2 = _resource_to_lock_id("execution:exec_001")
    assert id1 == id2


def test_resource_to_lock_id_different_keys():
    """Different keys should produce different lock IDs."""
    id1 = _resource_to_lock_id("execution:exec_001")
    id2 = _resource_to_lock_id("execution:exec_002")
    assert id1 != id2


def test_resource_to_lock_id_is_int():
    """Lock ID should be a signed 64-bit integer."""
    lock_id = _resource_to_lock_id("approval:apr_001")
    assert isinstance(lock_id, int)
    assert -(2**63) <= lock_id <= 2**63 - 1
