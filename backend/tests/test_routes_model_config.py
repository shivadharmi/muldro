import asyncio
from unittest.mock import MagicMock

import pytest
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
from src.models.model_binding import ModelBinding
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


def test_get_model_catalog():
    with _client() as c:
        r = c.get("/v1/model-catalog")
        assert r.status_code == 200
        body = r.json()
        assert "anthropic" in body["providers"]
        anthropic = body["providers"]["anthropic"]
        assert any(m["model_id"] == "claude-sonnet-4-6" for m in anthropic)
        assert all(
            {"model_id", "display_name", "thinking_style", "accepts_temperature", "suggested_tier"}
            <= set(m)
            for m in anthropic
        )


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


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_put_then_get_model_config():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))

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

    try:
        with TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "tier": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [
                        {
                            "tier": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put.status_code == 200, put.text

            # Re-PUT the same tier to exercise the UPSERT path (no unique-violation).
            put2 = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "tier": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "low",
                            "max_tokens": 2048,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert put2.status_code == 200, put2.text

            got = c.get("/v1/model-config")
            assert got.status_code == 200, got.text
            body = got.json()

            tiers = {t["tier"]: t for t in body["tiers"]}
            # Workspace override wins for balanced (re-PUT effort=low).
            assert tiers["balanced"]["model_id"] == "claude-sonnet-4-6"
            assert tiers["balanced"]["effort"] == "low"
            assert tiers["balanced"]["max_tokens"] == 2048
            # Untouched tiers fall through to the deployment defaults.
            assert tiers["reasoning"]["model_id"] == "claude-opus-4-8"
            assert tiers["fast"]["model_id"] == "claude-haiku-4-5-20251001"

            # Agent override round-trips with the agent name in the tier field.
            overrides = {o["tier"]: o for o in body["agent_overrides"]}
            assert overrides["planner"]["model_id"] == "claude-opus-4-8"

            # Provider statuses cover the whole catalog; none configured here.
            providers = {p["provider"]: p for p in body["providers"]}
            assert "anthropic" in providers
            assert providers["anthropic"]["configured"] is False
            assert providers["anthropic"]["status"] == "unconfigured"

            # Unknown model is rejected.
            bad = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "tier": "balanced",
                            "provider": "anthropic",
                            "model_id": "no-such-model",
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert bad.status_code == 400, bad.text
    finally:

        async def _cleanup():
            async with factory() as s:
                await s.execute(delete(ModelBinding).where(ModelBinding.workspace_id == ws))
                await s.commit()
            await engine.dispose()

        asyncio.run(_cleanup())
