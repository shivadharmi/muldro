"""Unit tests for the chat-permission-model entitlement helpers (P2.3).

* ``workspace_allows_bypass`` — the per-workspace opt-in gate for ``bypass`` mode.
  Fail-safe: True ONLY when ``settings.allow_bypass`` is truthy; False on unset / missing
  workspace / any error.
* ``AgentInvoker.has_durable_checkpointer`` — True ONLY for a durable ``AsyncPostgresSaver``
  (a pause spans two HTTP requests); False for ``MemorySaver`` / ``None``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.orchestrator.agent_invoker import AgentInvoker
from src.services.workspace_entitlements import workspace_allows_bypass

pytestmark = pytest.mark.asyncio


class _FakeDB:
    def __init__(self, workspace):
        self._workspace = workspace

    async def get(self, _model, _key):
        return self._workspace


class _FakeFactory:
    """Async-context-manager session factory yielding a ``_FakeDB``."""

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


# ── workspace_allows_bypass ───────────────────────────────────────────────────


async def test_allows_bypass_true_when_flag_truthy():
    ws = MagicMock()
    ws.settings = {"allow_bypass": True}
    assert await workspace_allows_bypass(_FakeFactory(ws), "ws_1") is True


async def test_allows_bypass_false_when_flag_false():
    ws = MagicMock()
    ws.settings = {"allow_bypass": False}
    assert await workspace_allows_bypass(_FakeFactory(ws), "ws_1") is False


async def test_allows_bypass_false_when_flag_absent():
    ws = MagicMock()
    ws.settings = {"other": 1}
    assert await workspace_allows_bypass(_FakeFactory(ws), "ws_1") is False


async def test_allows_bypass_false_when_settings_none():
    ws = MagicMock()
    ws.settings = None
    assert await workspace_allows_bypass(_FakeFactory(ws), "ws_1") is False


async def test_allows_bypass_false_when_workspace_missing():
    assert await workspace_allows_bypass(_FakeFactory(None), "ws_1") is False


async def test_allows_bypass_false_on_error():
    # Fail-safe: any DB error → False (never implicitly grant bypass).
    assert await workspace_allows_bypass(_RaisingFactory(), "ws_1") is False


# ── AgentInvoker.has_durable_checkpointer ─────────────────────────────────────


def _invoker_with_checkpointer(checkpointer):
    inv = AgentInvoker.__new__(AgentInvoker)
    inv._checkpointer_provider = lambda: checkpointer
    return inv


async def test_has_durable_checkpointer_true_for_postgres_saver():
    saver = MagicMock(spec=AsyncPostgresSaver)
    assert _invoker_with_checkpointer(saver).has_durable_checkpointer() is True


async def test_has_durable_checkpointer_false_for_memory_saver():
    assert _invoker_with_checkpointer(MemorySaver()).has_durable_checkpointer() is False


async def test_has_durable_checkpointer_false_for_none():
    assert _invoker_with_checkpointer(None).has_durable_checkpointer() is False
