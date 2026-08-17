"""§4.3 master-key startup guard: fail loud if encrypted provider credentials exist
but JARVIS_CONFIG_ENCRYPTION_KEY is unset.

Covers the query helper `has_encrypted_provider_credential` (real DB) plus the guard's
raise/no-raise decision logic (pure).
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
from src.services.model_config_registry import has_encrypted_provider_credential


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
    # Clear committed deployment-default (NULL-workspace) rows so this test's own
    # NULL-workspace inserts and the helper's global check are deterministic. These
    # deletes roll back with the test's transaction, leaving real defaults intact.
    await db.execute(delete(ModelBinding).where(ModelBinding.workspace_id.is_(None)))
    await db.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id.is_(None)))
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    db.add(User(user_id=uid, email=f"mkg-{suffix}@example.com", display_name="mkg"))
    db.add(Workspace(workspace_id=ws, name="mkg-ws", owner_user_id=uid))
    await db.flush()
    return ws


async def test_has_encrypted_true_when_ciphertext_present():
    async with _session() as db:
        await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="anthropic",
                api_key_encrypted="ciphertext",
                status="valid",
            )
        )
        await db.flush()
        assert await has_encrypted_provider_credential(db) is True


async def test_has_encrypted_false_when_all_null():
    async with _session() as db:
        await _seed_workspace(db)
        # A row with NULL api_key_encrypted must not count.
        db.add(
            ProviderCredential(
                workspace_id=None,
                provider="anthropic",
                api_key_encrypted=None,
                status="untested",
            )
        )
        await db.flush()
        assert await has_encrypted_provider_credential(db) is False


async def test_has_encrypted_false_when_no_rows():
    async with _session() as db:
        await _seed_workspace(db)
        assert await has_encrypted_provider_credential(db) is False


# --- Guard decision logic (pure): raise iff encrypted creds exist and no master key ---


def _guard_should_raise(has_encrypted_creds: bool, config_encryption_key: str) -> bool:
    return has_encrypted_creds and not config_encryption_key


@pytest.mark.parametrize(
    "has_creds,key,expected",
    [
        (True, "", True),  # creds + no key -> fail loud
        (True, "somekey", False),  # creds + key -> ok
        (False, "", False),  # no creds + no key -> ok
        (False, "somekey", False),  # no creds + key -> ok
    ],
)
def test_guard_decision(has_creds, key, expected):
    assert _guard_should_raise(has_creds, key) is expected
