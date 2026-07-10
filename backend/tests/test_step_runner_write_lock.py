# tests/test_step_runner_write_lock.py
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.services.step_runner import (
    _should_build_write_lock_wrapper,
    make_lock_wrapped_execute_tool_fn,
)
from src.services.write_lock import WriteLockContended, write_lock_key


class _FakeRedis:
    def __init__(self):
        self.keys = []

    async def set(self, k, v, nx=None, ex=None):
        self.keys.append(k)
        return True

    async def eval(self, *a):
        return 1


async def test_autonomous_write_acquires_the_same_key_as_deep_path():
    redis = _FakeRedis()

    # Mirrors the real idempotency inner: user_id/workspace_id are KEYWORD-ONLY.
    async def inner(tool_name, tool_input, *, user_id, workspace_id):
        return {"ok": True}

    async def resolve_capability(name):
        return "email.send"

    fn = make_lock_wrapped_execute_tool_fn(
        inner, redis=redis, workspace_id="ws1", resolve_capability=resolve_capability
    )
    # agent_loop's exact calling convention: positional name+args, keyword user_id/workspace_id.
    result = await fn("send_email", {}, user_id="u1", workspace_id="ws1")
    assert result == {"ok": True}
    assert f"lock:{write_lock_key('ws1', 'email.send')}" in redis.keys


async def test_autonomous_read_does_not_lock():
    redis = _FakeRedis()

    async def inner(tool_name, tool_input, *, user_id, workspace_id):
        return {"ok": True}

    async def resolve_capability(name):
        return "email.read"

    fn = make_lock_wrapped_execute_tool_fn(
        inner, redis=redis, workspace_id="ws1", resolve_capability=resolve_capability
    )
    await fn("list_email", {}, user_id="u1", workspace_id="ws1")
    assert redis.keys == []


async def test_contention_returns_blocked_envelope_not_raise():
    # When the lock is contended, the wrapper returns a structured blocked error (never
    # raises), so agent_loop treats it as a tool error rather than crashing the step. We
    # patch acquire_write_lock to raise WriteLockContended IMMEDIATELY (no bounded-wait),
    # keeping the test fast and free of real timing. The bounded-wait->raise timing itself
    # is proven in tests/test_write_lock.py; here we assert the wrapper's error contract.
    redis = _FakeRedis()

    async def inner(tool_name, tool_input, *, user_id, workspace_id):
        return {"ok": True}

    async def resolve_capability(name):
        return "email.send"

    @asynccontextmanager
    async def _always_contended(*a, **k):
        raise WriteLockContended("contended")
        yield  # pragma: no cover - unreachable; makes this a valid async CM

    # Patched before the factory runs so its ``from ... import acquire_write_lock`` binds
    # the stub (the import lives inside make_lock_wrapped_execute_tool_fn's body).
    with patch("src.services.write_lock.acquire_write_lock", _always_contended):
        fn = make_lock_wrapped_execute_tool_fn(
            inner, redis=redis, workspace_id="ws1", resolve_capability=resolve_capability
        )
        result = await fn("send_email", {}, user_id="u1", workspace_id="ws1")

    assert result.get("blocked") is True and "error" in result


# --- Step-10A A3: opt-in write_lock_require_redis (fail-closed when Redis is unwired) ---


async def test_require_redis_true_and_redis_none_write_is_refused():
    """flag ON + redis None + WRITE -> blocked, inner_fn NEVER called."""
    inner = AsyncMock(return_value={"ok": True})
    resolve_capability = AsyncMock(return_value="email.send")

    fn = make_lock_wrapped_execute_tool_fn(
        inner,
        redis=None,
        workspace_id="ws1",
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    result = await fn("send_email", {}, user_id="u1", workspace_id="ws1")

    inner.assert_not_awaited()
    assert result == {
        "error": "write refused — redis write-lock required but unavailable",
        "blocked": True,
    }


async def test_require_redis_true_and_redis_none_read_passes_through():
    """flag ON + redis None + READ -> passes through, inner_fn IS called."""
    inner = AsyncMock(return_value={"ok": True})
    resolve_capability = AsyncMock(return_value="email.read")

    fn = make_lock_wrapped_execute_tool_fn(
        inner,
        redis=None,
        workspace_id="ws1",
        resolve_capability=resolve_capability,
        require_redis=True,
    )
    result = await fn("list_email", {}, user_id="u1", workspace_id="ws1")

    inner.assert_awaited_once()
    assert result == {"ok": True}


async def test_require_redis_false_default_and_redis_none_write_executes_unlocked():
    """flag OFF (default) + redis None + WRITE -> byte-identical to today: inner_fn IS
    called (executes unlocked), and resolve_capability is NEVER called (strict
    byte-neutrality — the early-return happens before capability resolution)."""
    inner = AsyncMock(return_value={"ok": True})
    resolve_capability = AsyncMock(return_value="email.send")

    fn = make_lock_wrapped_execute_tool_fn(
        inner,
        redis=None,
        workspace_id="ws1",
        resolve_capability=resolve_capability,
        # require_redis omitted -> defaults False
    )
    result = await fn("send_email", {}, user_id="u1", workspace_id="ws1")

    inner.assert_awaited_once()
    resolve_capability.assert_not_awaited()
    assert result == {"ok": True}


# --- Step-10A A3: outer-gate predicate — build the wrapper under require_redis even with a
# None redis client, so the in-wrapper fail-closed branch is reachable on the autonomous path.


def test_predicate_redis_and_registry_present_builds():
    # Normal case: locking is possible → build.
    assert _should_build_write_lock_wrapper(object(), object(), require_redis=False) is True


def test_predicate_no_redis_flag_off_does_not_build_byte_neutral():
    # BYTE-NEUTRAL: with the flag off, a None redis reduces to the old gate → do NOT build.
    assert _should_build_write_lock_wrapper(None, object(), require_redis=False) is False


def test_predicate_no_redis_flag_on_builds_so_fail_closed_reachable():
    # NEW: operator opted into fail-closed → build even with a None redis client, so the
    # in-wrapper redis-None branch can refuse writes.
    assert _should_build_write_lock_wrapper(None, object(), require_redis=True) is True


def test_predicate_no_registry_never_builds():
    # No registry → cannot classify tools → never build, regardless of redis/flag.
    assert _should_build_write_lock_wrapper(object(), None, require_redis=False) is False
    assert _should_build_write_lock_wrapper(object(), None, require_redis=True) is False
    assert _should_build_write_lock_wrapper(None, None, require_redis=True) is False


async def test_wrapper_with_none_redis_and_require_redis_refuses_write_and_passes_read():
    # Reachability: the predicate lets StepRunner build the wrapper with redis=None; prove the
    # in-wrapper branch does the right thing without ever touching acquire_write_lock.
    inner = AsyncMock(return_value={"ok": True})

    fn_write = make_lock_wrapped_execute_tool_fn(
        inner,
        redis=None,
        workspace_id="ws",
        resolve_capability=AsyncMock(return_value="email.send"),
        require_redis=True,
    )
    result = await fn_write("send_email", {}, user_id="u1", workspace_id="ws")
    inner.assert_not_awaited()
    assert result == {
        "error": "write refused — redis write-lock required but unavailable",
        "blocked": True,
    }

    inner_read = AsyncMock(return_value={"ok": True})
    fn_read = make_lock_wrapped_execute_tool_fn(
        inner_read,
        redis=None,
        workspace_id="ws",
        resolve_capability=AsyncMock(return_value="email.read"),
        require_redis=True,
    )
    read_result = await fn_read("list_email", {}, user_id="u1", workspace_id="ws")
    inner_read.assert_awaited_once()
    assert read_result == {"ok": True}
