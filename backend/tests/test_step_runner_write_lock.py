# tests/test_step_runner_write_lock.py
from src.services.step_runner import make_lock_wrapped_execute_tool_fn
from src.services.write_lock import write_lock_key


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

    async def inner(name, args, user_id, ws):
        return {"ok": True}

    async def resolve_capability(name):
        return "email.send"

    fn = make_lock_wrapped_execute_tool_fn(
        inner, redis=redis, workspace_id="ws1", resolve_capability=resolve_capability
    )
    await fn("send_email", {}, "u1", "ws1")
    assert f"lock:{write_lock_key('ws1', 'email.send')}" in redis.keys


async def test_autonomous_read_does_not_lock():
    redis = _FakeRedis()

    async def inner(name, args, user_id, ws):
        return {"ok": True}

    async def resolve_capability(name):
        return "email.read"

    fn = make_lock_wrapped_execute_tool_fn(
        inner, redis=redis, workspace_id="ws1", resolve_capability=resolve_capability
    )
    await fn("list_email", {}, "u1", "ws1")
    assert redis.keys == []
