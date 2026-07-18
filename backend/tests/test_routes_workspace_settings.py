"""P3c: per-workspace default permission mode GET/PUT. Validates the value and JSONB-merges
without clobbering sibling keys (e.g. allow_bypass)."""

from __future__ import annotations

import pytest

from src.api.routes_workspace_settings import (
    DefaultPermissionModeRequest,
    _merged_settings,
)


def test_request_rejects_bad_value():
    with pytest.raises(ValueError):
        DefaultPermissionModeRequest(default_permission_mode="garbage")


def test_request_accepts_valid_values():
    for v in ("auto", "ask", "bypass"):
        assert DefaultPermissionModeRequest(default_permission_mode=v).default_permission_mode == v


def test_merged_settings_preserves_siblings():
    # Writing the default must not drop allow_bypass or other keys.
    merged = _merged_settings({"allow_bypass": True, "foo": 1}, "ask")
    assert merged == {"allow_bypass": True, "foo": 1, "default_permission_mode": "ask"}


def test_merged_settings_from_none():
    assert _merged_settings(None, "bypass") == {"default_permission_mode": "bypass"}


def test_merged_settings_does_not_mutate_input():
    original = {"allow_bypass": True}
    _merged_settings(original, "ask")
    assert original == {"allow_bypass": True}  # immutable — new dict returned
