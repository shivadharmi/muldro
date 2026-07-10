# tests/deep_runtime/test_write_lock_middleware.py
"""Step 6C Task 1.2: deep-runtime write-lock middleware.

The middleware is a ``@wrap_tool_call`` interceptor placed BETWEEN trust_gate (OUTER)
and jarvis_tool_dispatcher (INNER). It acquires the SHARED cross-path write lock
(``src.services.write_lock``) only around external WRITES; reads and built-ins pass
straight through and never touch Redis.

The decorated hook is exposed on the built ``AgentMiddleware`` as ``awrap_tool_call``
(the async form) — same invocation the trust_gate / capability_scope / dispatcher tests
use (see ``tests/deep_runtime/test_trust_gate.py``); ``mw.awrap_tool_call(request, handler)``.
"""

import json
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.write_lock import make_write_lock_middleware


class _FakeRedis:
    def __init__(self):
        self.calls = []

    async def set(self, k, v, nx=None, ex=None):
        self.calls.append(("set", k))
        return True

    async def eval(self, *a):
        self.calls.append(("eval",))
        return 1


class _Req:
    def __init__(self, name):
        self.tool_call = {"name": name, "id": "tc1", "args": {}}


async def test_write_capability_acquires_lock_around_handler():
    redis = _FakeRedis()

    async def resolve_capability(name):
        return "email.send"  # a write capability

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="tc1", name="x")

    mw = make_write_lock_middleware(
        workspace_id="ws1", redis=redis, resolve_capability=resolve_capability
    )
    result = await mw.awrap_tool_call(_Req("send_email"), handler)  # invoke the hook
    assert result.content == "ok"
    assert ("set", "lock:write:ws1:email.send") in redis.calls  # lock acquired
    assert ("eval",) in redis.calls  # lock released


async def test_read_capability_bypasses_lock():
    redis = _FakeRedis()

    async def resolve_capability(name):
        return "email.read"  # read-only

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="tc1", name="x")

    mw = make_write_lock_middleware(
        workspace_id="ws1", redis=redis, resolve_capability=resolve_capability
    )
    await mw.awrap_tool_call(_Req("list_email"), handler)
    assert redis.calls == []  # NEVER locked a read


# --- Step-10A A3: opt-in write_lock_require_redis (fail-closed when Redis is unwired) ---


async def test_require_redis_true_and_redis_none_write_is_refused():
    """flag ON + redis None + WRITE -> blocked, handler NEVER called."""
    resolve_capability = AsyncMock(return_value="email.send")
    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tc1", name="x"))

    mw = make_write_lock_middleware(
        workspace_id="ws1",
        redis=None,
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    result = await mw.awrap_tool_call(_Req("send_email"), handler)

    handler.assert_not_awaited()
    assert result.status == "error"
    body = json.loads(result.content)
    assert body["blocked"] is True
    assert "redis write-lock required but unavailable" in body["error"]


async def test_require_redis_true_and_redis_none_read_passes_through():
    """flag ON + redis None + READ -> passes through, handler IS called."""
    resolve_capability = AsyncMock(return_value="email.read")
    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tc1", name="x"))

    mw = make_write_lock_middleware(
        workspace_id="ws1",
        redis=None,
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    result = await mw.awrap_tool_call(_Req("list_email"), handler)

    handler.assert_awaited_once()
    assert result.content == "ok"


async def test_require_redis_false_default_and_redis_none_write_executes_unlocked():
    """flag OFF (default) + redis None + WRITE -> byte-identical to today: handler IS
    called (executes unlocked), and resolve_capability is NEVER called (strict
    byte-neutrality — the early-return happens before capability resolution)."""
    resolve_capability = AsyncMock(return_value="email.send")
    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tc1", name="x"))

    mw = make_write_lock_middleware(
        workspace_id="ws1",
        redis=None,
        resolve_capability=resolve_capability,
        # require_redis omitted -> defaults False
    )
    result = await mw.awrap_tool_call(_Req("send_email"), handler)

    handler.assert_awaited_once()
    resolve_capability.assert_not_awaited()
    assert result.content == "ok"
