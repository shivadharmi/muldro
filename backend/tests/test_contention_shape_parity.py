# tests/test_contention_shape_parity.py
"""Step-10A A7: canonical blocked-write body shared by BOTH execution paths.

``src.services.contention.blocked_body`` is the single source of the "this write did not
run" body. The deep-runtime write-lock middleware wraps it in a langchain ``ToolMessage``
(status="error"); the autonomous step-runner wrapper returns it as a bare dict. These tests
pin the exact body strings AND drive both real wrappers to the contended / fail-closed
branches to prove the two paths can never drift apart.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.write_lock import make_write_lock_middleware
from src.services.contention import (
    CONTENDED_MESSAGE,
    WRITE_LOCK_UNAVAILABLE_MESSAGE,
    blocked_body,
)
from src.services.step_runner import make_lock_wrapped_execute_tool_fn
from src.services.write_lock import WriteLockContended


class _Req:
    def __init__(self, name):
        self.tool_call = {"name": name, "id": "tc1", "args": {}}


def test_blocked_body_pins_contended_message():
    assert blocked_body(CONTENDED_MESSAGE) == {
        "error": "resource busy — another write is in progress, retry",
        "blocked": True,
    }


def test_blocked_body_pins_write_lock_unavailable_message():
    assert blocked_body(WRITE_LOCK_UNAVAILABLE_MESSAGE) == {
        "error": "write refused — redis write-lock required but unavailable",
        "blocked": True,
    }


@asynccontextmanager
async def _always_contended(*a, **k):
    raise WriteLockContended("contended")
    yield  # pragma: no cover - unreachable; makes this a valid async CM


async def test_contended_body_identical_across_both_paths():
    """Drive the deep middleware AND the autonomous wrapper to the WriteLockContended
    branch and assert both produce a body equal to blocked_body(CONTENDED_MESSAGE)."""

    async def resolve_capability(name):
        return "email.send"  # a write capability

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="tc1", name="x")  # pragma: no cover

    mw = make_write_lock_middleware(
        workspace_id="ws1", redis=AsyncMock(), resolve_capability=resolve_capability
    )
    with patch("src.deep_runtime.middleware.write_lock.acquire_write_lock", _always_contended):
        deep_result = await mw.awrap_tool_call(_Req("send_email"), handler)

    assert deep_result.status == "error"
    deep_body = json.loads(deep_result.content)

    async def inner(tool_name, tool_input, *, user_id, workspace_id):
        return {"ok": True}  # pragma: no cover

    with patch("src.services.write_lock.acquire_write_lock", _always_contended):
        fn = make_lock_wrapped_execute_tool_fn(
            inner, redis=AsyncMock(), workspace_id="ws1", resolve_capability=resolve_capability
        )
        autonomous_body = await fn("send_email", {}, user_id="u1", workspace_id="ws1")

    assert deep_body == autonomous_body == blocked_body(CONTENDED_MESSAGE)


async def test_write_lock_unavailable_body_identical_across_both_paths():
    """Drive the deep middleware AND the autonomous wrapper to the require_redis
    fail-closed branch (redis=None) and assert both produce a body equal to
    blocked_body(WRITE_LOCK_UNAVAILABLE_MESSAGE)."""
    resolve_capability = AsyncMock(return_value="email.send")

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="tc1", name="x")  # pragma: no cover

    mw = make_write_lock_middleware(
        workspace_id="ws1",
        redis=None,
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    deep_result = await mw.awrap_tool_call(_Req("send_email"), handler)
    assert deep_result.status == "error"
    deep_body = json.loads(deep_result.content)

    inner = AsyncMock(return_value={"ok": True})
    fn = make_lock_wrapped_execute_tool_fn(
        inner,
        redis=None,
        workspace_id="ws1",
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    autonomous_body = await fn("send_email", {}, user_id="u1", workspace_id="ws1")

    assert deep_body == autonomous_body == blocked_body(WRITE_LOCK_UNAVAILABLE_MESSAGE)
