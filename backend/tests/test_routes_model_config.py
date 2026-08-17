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

            # Provider statuses cover the whole catalog. Anthropic has no
            # credential row here, but the deployment env key (JARVIS_ANTHROPIC_API_KEY)
            # is set, so it reports as an env-backed working default. A provider with
            # neither a row nor an env key (ollama uses base_url only) is unconfigured.
            providers = {p["provider"]: p for p in body["providers"]}
            assert "anthropic" in providers
            assert providers["anthropic"]["configured"] is True
            assert providers["anthropic"]["status"] == "valid"
            assert providers["ollama"]["configured"] is False
            assert providers["ollama"]["status"] == "unconfigured"

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


def _cred_cleanup(engine, factory, ws: str):
    async def _cleanup():
        async with factory() as s:
            await s.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id == ws))
            await s.commit()
        await engine.dispose()

    asyncio.run(_cleanup())


def _use_test_key(monkeypatch):
    from cryptography.fernet import Fernet

    from src.config import secret_crypto

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_put_credential_stores_encrypted_and_masks(monkeypatch):
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            r = c.put("/v1/providers/openai/credentials", json={"api_key": "sk-secret-123"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["configured"] is True
            assert body["provider"] == "openai"
            assert body["status"] == "untested"
            # The key is never echoed in the write response.
            assert "sk-secret-123" not in r.text

            got = c.get("/v1/model-config")
            assert got.status_code == 200, got.text
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["openai"]["configured"] is True
            assert prov["openai"]["status"] == "untested"
            # The key never appears in any response payload.
            assert "sk-secret-123" not in got.text

            # The stored value is a Fernet ciphertext, not the plaintext.
            async def _check_stored():
                from sqlalchemy import select

                async with factory() as s:
                    row = (
                        (
                            await s.execute(
                                select(ProviderCredential).where(
                                    ProviderCredential.workspace_id == ws,
                                    ProviderCredential.provider == "openai",
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return row.api_key_encrypted

            stored = asyncio.run(_check_stored())
            assert stored is not None
            assert stored != "sk-secret-123"

            # Re-PUT exercises the UPSERT path (no unique-violation) and resets status.
            r2 = c.put("/v1/providers/openai/credentials", json={"api_key": "sk-second-456"})
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "untested"

            # Unknown provider is rejected.
            bad = c.put("/v1/providers/no-such/credentials", json={"api_key": "x"})
            assert bad.status_code == 400, bad.text
    finally:
        _cred_cleanup(engine, factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_delete_credential_unconfigures(monkeypatch):
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            put = c.put("/v1/providers/openai/credentials", json={"api_key": "sk-secret-123"})
            assert put.status_code == 200, put.text

            deleted = c.delete("/v1/providers/openai/credentials")
            assert deleted.status_code == 200, deleted.text
            dbody = deleted.json()
            assert dbody["configured"] is False
            assert dbody["status"] == "unconfigured"

            got = c.get("/v1/model-config")
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["openai"]["configured"] is False
            assert prov["openai"]["status"] == "unconfigured"

            # Deleting an already-absent credential is a no-op success.
            again = c.delete("/v1/providers/openai/credentials")
            assert again.status_code == 200, again.text
            assert again.json()["configured"] is False
    finally:
        _cred_cleanup(engine, factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_test_credential_valid_and_fail_closed(monkeypatch):
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            # Not configured -> invalid, no 500.
            unconf = c.post("/v1/providers/openai/test")
            assert unconf.status_code == 200, unconf.text
            assert unconf.json()["status"] == "invalid"

            put = c.put("/v1/providers/openai/credentials", json={"api_key": "sk-secret-123"})
            assert put.status_code == 200, put.text

            # Happy path: fake model whose ainvoke succeeds -> status "valid".
            class _FakeModel:
                async def ainvoke(self, _msg):
                    return MagicMock()

            monkeypatch.setattr(
                "src.api.routes_model_config.build_langchain_model", lambda resolved: _FakeModel()
            )
            ok = c.post("/v1/providers/openai/test")
            assert ok.status_code == 200, ok.text
            assert ok.json()["status"] == "valid"

            got = c.get("/v1/model-config")
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["openai"]["status"] == "valid"

            # Bad key: model raises -> status "invalid", never a 500.
            class _BoomModel:
                async def ainvoke(self, _msg):
                    raise RuntimeError("401 unauthorized: invalid api key")

            monkeypatch.setattr(
                "src.api.routes_model_config.build_langchain_model", lambda resolved: _BoomModel()
            )
            boom = c.post("/v1/providers/openai/test")
            assert boom.status_code == 200, boom.text
            assert boom.json()["status"] == "invalid"
            assert "detail" in boom.json()
    finally:
        _cred_cleanup(engine, factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_test_credential_does_not_leak_provider_exception(monkeypatch):
    """A provider SDK exception must not have its text returned to the client (M8)."""
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            put = c.put("/v1/providers/openai/credentials", json={"api_key": "sk-secret-123"})
            assert put.status_code == 200, put.text

            class _LeakyModel:
                async def ainvoke(self, _msg):
                    raise Exception("SENSITIVE-INTERNAL-abcdef-endpoint-https://secret")

            monkeypatch.setattr(
                "src.api.routes_model_config.build_langchain_model",
                lambda resolved: _LeakyModel(),
            )
            r = c.post("/v1/providers/openai/test")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "invalid"
            assert body["detail"] == "credential invalid"
            assert "SENSITIVE" not in (body["detail"] or "")
            assert "secret" not in (body["detail"] or "")
            assert "SENSITIVE" not in r.text
            assert "secret" not in r.text
    finally:
        _cred_cleanup(engine, factory, ws)
