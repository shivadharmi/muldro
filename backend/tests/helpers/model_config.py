"""Shared fixtures for the split model-config route tests.

These build a real `create_app()` TestClient wired to a workspace-scoped DB
session (`tests/test_routes_model_config.py` and
`tests/test_routes_provider_credentials.py` both need the same app-building
and workspace/credential-seeding scaffolding, so it lives here rather than
being duplicated or forcing the two test modules to import each other).
"""

import asyncio
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.api.app import create_app
from src.api.deps import (
    get_current_user,
    get_current_user_id,
    get_current_workspace_id,
    get_session,
)
from src.config.settings import get_settings
from src.models.provider_credential import ProviderCredential
from src.models.users import User, Workspace
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


def _client():
    app = create_app()
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_current_workspace_id] = lambda: TEST_WORKSPACE_ID
    return TestClient(app)


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe():
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


async def _seed_ws(factory) -> str:
    suffix = str(ULID())
    ws = f"ws_{suffix}"
    async with factory() as db:
        uid = f"usr_{suffix}"
        db.add(User(user_id=uid, email=f"mc-{suffix}@example.com", display_name="mc"))
        db.add(Workspace(workspace_id=ws, name="mc-ws", owner_user_id=uid))
        await db.commit()
    return ws


def _ws_app(factory, ws):
    """A TestClient app bound to workspace *ws* and session factory *factory*.

    Every /v1/model-config test needs a real session: FastAPI solves the get_session
    dependency before it reports a body-validation error, so even a 422 test would blow
    up on a missing DB without this.
    """

    async def _override():
        async with factory() as s:
            yield s

    app = create_app()
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_current_workspace_id] = lambda: ws
    app.dependency_overrides[get_session] = _override
    return app


def _ws_factory():
    """(factory, workspace_id) for a freshly seeded workspace."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, asyncio.run(_seed_ws(factory))


def _cred_app(ws: str, factory):
    """Build an app wired to a real DB session factory and a fixed workspace."""

    async def _override():
        async with factory() as s:
            yield s

    app = create_app()
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_current_workspace_id] = lambda: ws
    app.dependency_overrides[get_session] = _override
    return app


def _use_test_key(monkeypatch):
    from cryptography.fernet import Fernet

    from src.config import secret_crypto

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)


def _delete_ws_credentials(factory, ws: str):
    """Delete every ProviderCredential row this test's workspace owns.

    Rows encrypted under a per-test random Fernet key (see `_use_test_key`) can never
    be decrypted once the test ends, and create_app()'s unscoped master-key guard
    (src/api/app.py) 500s at boot for the WHOLE suite if any encrypted row survives
    while MULDRO_CONFIG_ENCRYPTION_KEY is unset -- so this cleanup is not optional
    hygiene, it is what keeps later tests bootable.
    """

    async def _cleanup():
        async with factory() as db:
            await db.execute(
                delete(ProviderCredential).where(ProviderCredential.workspace_id == ws)
            )
            await db.commit()

    asyncio.run(_cleanup())
