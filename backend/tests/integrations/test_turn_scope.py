from src.integrations.turn_scope import (
    TurnScope,
    current_turn_scope,
    turn_scope,
)


def test_register_and_acquire_refcounts():
    scope = TurnScope()
    scope.register(("ws", "github", "u1"))
    scope.acquire(("ws", "github", "u1"))
    assert scope.refcount(("ws", "github", "u1")) == 2


async def test_turn_scope_sets_and_clears_contextvar():
    assert current_turn_scope() is None
    closed = []
    async with turn_scope(on_close=lambda keys: closed.append(keys)) as scope:
        assert current_turn_scope() is scope
        scope.register(("ws", "slack", "u1"))
    assert current_turn_scope() is None
    assert closed == [[("ws", "slack", "u1")]]


async def test_close_only_returns_keys_at_positive_refcount():
    captured = []
    async with turn_scope(on_close=lambda keys: captured.append(list(keys))):
        scope = current_turn_scope()
        scope.register(("ws", "a", "u"))
        scope.register(("ws", "b", "u"))
        scope.acquire(("ws", "a", "u"))
        scope.release_one(("ws", "a", "u"))
    assert sorted(captured[0]) == [("ws", "a", "u"), ("ws", "b", "u")]
