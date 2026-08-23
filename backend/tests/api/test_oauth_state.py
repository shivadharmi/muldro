"""The OAuth `state` parameter must be an unguessable, single-use CSRF token.

It carried the raw `user_id`: a value that is not secret (it appears in logs,
URLs and API responses) and that the callback trusted as the identity to file
the resulting credential under. These tests pin the three properties the
replacement has to hold, each of which the old scheme failed.
"""

import pytest

from src.api.oauth_state import STATE_TTL_SECONDS, consume_state, issue_state


class _FakeRedis:
    """Minimal Redis with the SET NX / GETDEL semantics the module relies on."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def getdel(self, key):
        return self.store.pop(key, None)


async def test_a_state_is_not_the_user_id():
    """The defect verbatim: user ids are not secret, and state must be."""
    redis = _FakeRedis()
    state = await issue_state(redis, "usr_01JTEST")

    assert state != "usr_01JTEST"
    assert "usr_" not in state
    # 32 random bytes, urlsafe-encoded.
    assert len(state) >= 40


async def test_two_states_for_the_same_user_never_collide():
    redis = _FakeRedis()
    first = await issue_state(redis, "usr_01JTEST")
    second = await issue_state(redis, "usr_01JTEST")
    assert first != second


async def test_a_state_resolves_to_the_user_who_asked_for_it():
    redis = _FakeRedis()
    state = await issue_state(redis, "usr_01JTEST")
    assert await consume_state(redis, state) == "usr_01JTEST"


async def test_a_state_is_single_use():
    """A replayed callback would re-file a credential the founder already saw."""
    redis = _FakeRedis()
    state = await issue_state(redis, "usr_01JTEST")

    assert await consume_state(redis, state) == "usr_01JTEST"
    assert await consume_state(redis, state) is None


async def test_an_unknown_state_is_refused():
    redis = _FakeRedis()
    assert await consume_state(redis, "not-a-real-state") is None
    assert await consume_state(redis, "") is None


async def test_a_forged_user_id_shaped_state_is_refused():
    """The old scheme ACCEPTED this. That is the whole vulnerability."""
    redis = _FakeRedis()
    assert await consume_state(redis, "usr_01SOMEONEELSE") is None


async def test_issuing_without_redis_raises_rather_than_degrading():
    """Fail closed: a silent fallback is the same defect with a smaller window."""
    with pytest.raises(RuntimeError):
        await issue_state(None, "usr_01JTEST")


async def test_consuming_without_redis_refuses():
    assert await consume_state(None, "anything") is None


async def test_the_binding_expires():
    redis = _FakeRedis()
    state = await issue_state(redis, "usr_01JTEST")
    from src.api.oauth_state import _key

    assert redis.ttls[_key(state)] == STATE_TTL_SECONDS
