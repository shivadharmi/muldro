import asyncio
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config import secret_crypto
from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.models.users import User, Workspace
from src.services.model_resolver import ModelConfigError, ModelResolver, ResolvedModel


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


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_workspace(db) -> str:
    # Clear any committed deployment-default (NULL-workspace) config rows so this
    # test's own NULL-workspace inserts don't collide with the startup seed that an
    # app-lifespan test (e.g. an API test using create_app()) may have committed.
    # These deletes roll back with the test's transaction, leaving real defaults intact.
    await db.execute(delete(ModelBinding).where(ModelBinding.workspace_id.is_(None)))
    await db.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id.is_(None)))
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    db.add(User(user_id=uid, email=f"mr-{suffix}@example.com", display_name="mr"))
    db.add(Workspace(workspace_id=ws, name="mr-ws", owner_user_id=uid))
    await db.flush()
    return ws


async def test_tier_default_resolves(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="anthropic",
                api_key_encrypted=secret_crypto.encrypt_secret("sk-x"),
                status="valid",
            )
        )
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
        r = await ModelResolver(db).resolve(tier="balanced", workspace_id=ws)
        assert isinstance(r, ResolvedModel)
        assert r.provider == "anthropic" and r.model_id == "claude-sonnet-4-6"
        assert r.api_key == "sk-x"
        assert r.kwargs["thinking"]["type"] == "enabled"  # capability-mapped legacy thinking


async def test_agent_override_beats_tier(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="openai",
                api_key_encrypted=secret_crypto.encrypt_secret("sk-o"),
                status="valid",
            )
        )
        db.add(
            ModelBinding(
                workspace_id=None,
                scope_type="agent",
                scope_key="planner",
                provider="openai",
                model_id="gpt-5",
                effort="high",
                max_tokens=8192,
            )
        )
        await db.flush()
        r = await ModelResolver(db).resolve(
            agent="planner", agent_tier="reasoning", workspace_id=ws
        )
        assert r.provider == "openai" and r.model_id == "gpt-5"


async def test_resolved_model_id_returns_binding_model():
    """resolved_model_id returns the binding's model_id with no credential work."""
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
        mid = await ModelResolver(db).resolved_model_id(tier="balanced", workspace_id=ws)
        assert mid == "claude-sonnet-4-6"


async def test_resolved_model_id_none_when_no_binding():
    """No binding for the tier -> None (caller falls back to the tier default)."""
    async with _session() as db:
        ws = await _seed_workspace(db)
        mid = await ModelResolver(db).resolved_model_id(tier="nonexistent", workspace_id=ws)
        assert mid is None


async def test_missing_credential_raises(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
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
        with pytest.raises(ModelConfigError):
            await ModelResolver(db).resolve(tier="fast", workspace_id=ws)


async def test_override_missing_credential_degrades_to_tier(monkeypatch):
    """§10: a per-agent override whose provider credential is missing falls back to
    the tier binding rather than breaking the turn."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    # No openai env fallback, so the override provider is genuinely unconfigured.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    async with _session() as db:
        ws = await _seed_workspace(db)
        # Tier default: anthropic (configured via encrypted credential).
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="anthropic",
                api_key_encrypted=secret_crypto.encrypt_secret("sk-a"),
                status="valid",
            )
        )
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
        # Agent override: openai with NO credential (no row, no env).
        db.add(
            ModelBinding(
                workspace_id=None,
                scope_type="agent",
                scope_key="planner",
                provider="openai",
                model_id="gpt-5",
                effort="high",
                max_tokens=8192,
            )
        )
        await db.flush()
        r = await ModelResolver(db).resolve(agent="planner", agent_tier="balanced", workspace_id=ws)
        # Degraded to the tier default, not the broken override.
        assert r.provider == "anthropic" and r.model_id == "claude-sonnet-4-6"
        assert r.api_key == "sk-a"


async def test_override_with_credential_still_resolves_to_override(monkeypatch):
    """Happy path unchanged: a valid override wins over the tier binding."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="anthropic",
                api_key_encrypted=secret_crypto.encrypt_secret("sk-a"),
                status="valid",
            )
        )
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="openai",
                api_key_encrypted=secret_crypto.encrypt_secret("sk-o"),
                status="valid",
            )
        )
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
        db.add(
            ModelBinding(
                workspace_id=None,
                scope_type="agent",
                scope_key="planner",
                provider="openai",
                model_id="gpt-5",
                effort="high",
                max_tokens=8192,
            )
        )
        await db.flush()
        r = await ModelResolver(db).resolve(agent="planner", agent_tier="balanced", workspace_id=ws)
        assert r.provider == "openai" and r.model_id == "gpt-5"
        assert r.api_key == "sk-o"
