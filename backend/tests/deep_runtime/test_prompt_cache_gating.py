"""Gate the LIVE system-prompt ``cache_control`` marker by provider (H1).

The chat path stamps a block-level ``cache_control: {"type": "ephemeral"}`` marker on
the first system block unconditionally (``AgentInvoker.build_system_prompt``). That
marker is Anthropic-only; on a non-Anthropic resolved model it puts an Anthropic key on
the system block. ``build_deep_agent`` is the single chokepoint that both resolves the
real model and receives the system prompt, so it strips the marker when the backing
model does not support prompt caching.

Covered here:
  (a) ``strip_cache_control`` removes ``cache_control`` from a ``SystemMessage`` with
      block content and leaves a plain-string / None prompt untouched.
  (b) ``ModelResolver.supports_prompt_cache`` returns True for an Anthropic binding and
      False for an OpenAI binding (real DB).
  (c) ``build_deep_agent`` strips the marker when the resolver says caching is
      unsupported, and retains it when supported (byte-identical Anthropic path).
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import SystemMessage
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.prompt_bridge import strip_cache_control
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.models.users import User, Workspace
from src.services.model_resolver import ModelResolver

# ---------------------------------------------------------------------------
# (a) strip_cache_control — pure helper, no DB
# ---------------------------------------------------------------------------


def test_strip_cache_control_removes_marker_from_blocks():
    msg = SystemMessage(
        content=[
            {"type": "text", "text": "soul+role", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "context"},
        ]
    )
    out = strip_cache_control(msg)
    assert isinstance(out, SystemMessage)
    assert all("cache_control" not in b for b in out.content if isinstance(b, dict))
    # Text payload is preserved.
    assert [b["text"] for b in out.content] == ["soul+role", "context"]


def test_strip_cache_control_leaves_plain_string_untouched():
    assert strip_cache_control("just a string") == "just a string"
    assert strip_cache_control(None) is None
    # A SystemMessage with plain-string content is returned unchanged too.
    msg = SystemMessage(content="plain")
    assert strip_cache_control(msg) is msg


# ---------------------------------------------------------------------------
# (b) + (c) need a real DB
# ---------------------------------------------------------------------------


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:
        return False


_needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_workspace(db) -> str:
    # Clear any committed deployment-default (NULL-workspace) config rows so this
    # test's own NULL-workspace inserts don't collide with the app-lifespan startup seed.
    # These deletes roll back with the test's transaction, leaving real defaults intact.
    await db.execute(delete(ModelBinding).where(ModelBinding.workspace_id.is_(None)))
    await db.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id.is_(None)))
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    db.add(User(user_id=uid, email=f"pcg-{suffix}@example.com", display_name="pcg"))
    db.add(Workspace(workspace_id=ws, name="pcg-ws", owner_user_id=uid))
    await db.flush()
    return ws


@_needs_db
async def test_supports_prompt_cache_true_for_anthropic():
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ModelBinding(
                workspace_id=None,
                scope_type="tier",
                scope_key="balanced",
                provider="anthropic",
                model_id="claude-sonnet-4-6",
                effort="medium",
                max_tokens=4096,
            )
        )
        await db.flush()
        assert await ModelResolver(db).supports_prompt_cache(tier="balanced", workspace_id=ws)


@_needs_db
async def test_supports_prompt_cache_false_for_openai():
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ModelBinding(
                workspace_id=None,
                scope_type="tier",
                scope_key="fast",
                provider="openai",
                model_id="gpt-5-mini",
                effort="none",
                max_tokens=2048,
            )
        )
        await db.flush()
        assert not await ModelResolver(db).supports_prompt_cache(tier="fast", workspace_id=ws)


@_needs_db
async def test_supports_prompt_cache_true_when_no_binding():
    # No binding at all -> Anthropic-safe default (True).
    async with _session() as db:
        ws = await _seed_workspace(db)
        await db.flush()
        assert await ModelResolver(db).supports_prompt_cache(tier="nonexistent", workspace_id=ws)


# ---------------------------------------------------------------------------
# (c) build_deep_agent end-to-end gating
# ---------------------------------------------------------------------------


def _make_agent():
    from src.orchestrator.agents import SubAgent

    return SubAgent(
        name="perceiver",
        prompt="role prompt",
        model_tier="balanced",
        capability_scope=[],  # read-only -> no fail-closed write guard
    )


def _system_prompt_with_marker() -> SystemMessage:
    return SystemMessage(
        content=[
            {"type": "text", "text": "soul", "cache_control": {"type": "ephemeral"}},
        ]
    )


async def _build_and_capture(monkeypatch, *, supports_cache: bool):
    from src.deep_runtime import agent_builder

    captured: dict = {}

    def _fake_create_deep_agent(**kwargs):
        captured["system_prompt"] = kwargs["system_prompt"]
        return object()

    async def _fake_build_chat_model(agent, *, workspace_id, db_factory):
        return object()

    async def _fake_supports(self, **kwargs):
        return supports_cache

    monkeypatch.setattr(agent_builder, "create_deep_agent", _fake_create_deep_agent)
    monkeypatch.setattr(agent_builder, "build_chat_model", _fake_build_chat_model)
    monkeypatch.setattr(ModelResolver, "supports_prompt_cache", _fake_supports)

    @asynccontextmanager
    async def _db_factory():
        yield object()

    await agent_builder.build_deep_agent(
        _make_agent(),
        tools=[],
        workspace_id="ws_x",
        db_factory=_db_factory,
        system_prompt=_system_prompt_with_marker(),
    )
    return captured["system_prompt"]


async def test_build_deep_agent_strips_marker_when_unsupported(monkeypatch):
    sp = await _build_and_capture(monkeypatch, supports_cache=False)
    assert isinstance(sp, SystemMessage)
    assert all("cache_control" not in b for b in sp.content if isinstance(b, dict))


async def test_build_deep_agent_retains_marker_when_supported(monkeypatch):
    sp = await _build_and_capture(monkeypatch, supports_cache=True)
    assert isinstance(sp, SystemMessage)
    assert any(isinstance(b, dict) and "cache_control" in b for b in sp.content)
