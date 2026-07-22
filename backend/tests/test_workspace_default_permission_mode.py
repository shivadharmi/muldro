"""P3c: per-workspace default permission mode, read from Workspace.settings JSONB (no migration).
Fail-safe to ``"auto"`` on unset / bad value / missing workspace / DB error."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.workspace_entitlements import workspace_default_permission_mode

pytestmark = pytest.mark.asyncio


class _FakeDB:
    def __init__(self, workspace):
        self._workspace = workspace

    async def get(self, _model, _key):
        return self._workspace


class _FakeFactory:
    def __init__(self, workspace):
        self._workspace = workspace

    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeDB(self._workspace)

    async def __aexit__(self, *_a):
        return False


class _RaisingFactory:
    def __call__(self):
        raise RuntimeError("db down")


async def test_returns_stored_value():
    ws = MagicMock()
    ws.settings = {"default_permission_mode": "ask"}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "ask"


async def test_defaults_auto_when_absent():
    ws = MagicMock()
    ws.settings = {"allow_bypass": True}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_settings_none():
    ws = MagicMock()
    ws.settings = None
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_value_invalid():
    ws = MagicMock()
    ws.settings = {"default_permission_mode": "garbage"}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_workspace_missing():
    assert await workspace_default_permission_mode(_FakeFactory(None), "ws_1") == "auto"


async def test_defaults_auto_on_error():
    assert await workspace_default_permission_mode(_RaisingFactory(), "ws_1") == "auto"
