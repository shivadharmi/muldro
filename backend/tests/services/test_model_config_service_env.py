"""Env-backed provider credentials count as configured in the model config.

The behavior-preserving deployment seed uses the ``MULDRO_ANTHROPIC_API_KEY``
env var with NO ProviderCredential row. ``_provider_statuses`` must therefore
treat a provider whose env fallback key is set as ``configured=True`` /
``status="valid"`` even when no credential row exists — otherwise the seeded
tier's own provider is missing from the settings UI dropdown.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.models.users import User, Workspace
from src.services.model_config_service import ModelConfigService


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
    # Clear any committed NULL-workspace ProviderCredential rows (an app-lifespan
    # seed may have inserted them) so this test observes the pure env fallback.
    # The deletes roll back with the test's uncommitted transaction.
    await db.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id.is_(None)))
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    db.add(User(user_id=uid, email=f"mc-{suffix}@example.com", display_name="mc"))
    db.add(Workspace(workspace_id=ws, name="mc-ws", owner_user_id=uid))
    await db.flush()
    return ws


async def test_env_backed_provider_is_configured(monkeypatch):
    """Anthropic with an env key but NO credential row reports configured/valid;
    a provider with neither a row nor an env key reports unconfigured."""
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-env-anthropic")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "google_api_key", "")

    async with _session() as db:
        ws = await _seed_workspace(db)
        resp = await ModelConfigService(db).get_config_response(ws)
        providers = {p.provider: p for p in resp.providers}

        # Env-backed anthropic (no row) -> configured/valid.
        assert providers["anthropic"].configured is True
        assert providers["anthropic"].status == "valid"

        # openai has an env attr but the key is empty -> unconfigured.
        assert providers["openai"].configured is False
        assert providers["openai"].status == "unconfigured"

        # ollama has no env attr at all -> unconfigured (base_url only).
        assert providers["ollama"].configured is False
        assert providers["ollama"].status == "unconfigured"


async def test_legacy_invalid_effort_is_coerced_to_none():
    """Guards the legacy-row coercion path in ``_to_binding_dto``.

    ``effort`` was an unvalidated str before ModelBindingDTO's Literal, and the DB
    column still has no CHECK constraint, so a row can hold anything -- e.g.
    ``seed_defaults()`` writes ModelBinding rows straight from tuples, bypassing
    ModelBindingDTO/Pydantic entirely. The row here is inserted directly (bypassing
    the API) precisely because the API can no longer produce one with an invalid
    effort. ``get_config_response`` must coerce it to "none" rather than raising.
    """
    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ModelBinding(
                workspace_id=ws,
                scope_type="tier",
                scope_key="balanced",
                provider="anthropic",
                model_id="claude-sonnet-4-6",
                effort="bogus",
                max_tokens=4096,
            )
        )
        await db.flush()

        resp = await ModelConfigService(db).get_config_response(ws)
        tiers = {t.scope_key: t for t in resp.tiers}
        assert tiers["balanced"].effort == "none"
