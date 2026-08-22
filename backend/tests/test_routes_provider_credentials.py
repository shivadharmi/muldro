"""Tests for PUT/DELETE/POST /v1/providers/{provider}/credentials."""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.routes_model_config import merge_extra_config
from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from tests.helpers.model_config import (
    _cred_app,
    _db_reachable,
    _delete_ws_credentials,
    _seed_ws,
    _use_test_key,
    _ws_app,
    _ws_factory,
    pinned_deployment_defaults,
)


def _cred_cleanup(engine, factory, ws: str):
    async def _cleanup():
        async with factory() as s:
            await s.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id == ws))
            await s.commit()
        await engine.dispose()

    asyncio.run(_cleanup())


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
    # Deleting the stored credential only reads as "unconfigured" when there is no env
    # fallback behind it. `resolve_credential` consults settings last, so a developer with
    # MULDRO_OPENAI_API_KEY set would see the provider stay configured after the delete.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
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
            assert dbody["status"]["configured"] is False
            assert dbody["status"]["status"] == "unconfigured"

            got = c.get("/v1/model-config")
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["openai"]["configured"] is False
            assert prov["openai"]["status"] == "unconfigured"

            # Deleting an already-absent credential is a no-op success.
            again = c.delete("/v1/providers/openai/credentials")
            assert again.status_code == 200, again.text
            assert again.json()["status"]["configured"] is False
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


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_test_credential_uses_env_fallback(monkeypatch):
    """A provider configured only via its env key (no DB row) must test through the
    same env fallback ModelResolver uses — not report itself unconfigured (F4)."""
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            # anthropic has no credential row here, but MULDRO_ANTHROPIC_API_KEY is
            # set in the env — GET reports it configured, so /test must agree.
            got = c.get("/v1/model-config")
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["anthropic"]["configured"] is True

            class _FakeModel:
                async def ainvoke(self, _msg):
                    return MagicMock()

            monkeypatch.setattr(
                "src.api.routes_model_config.build_langchain_model", lambda resolved: _FakeModel()
            )
            r = c.post("/v1/providers/anthropic/test")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "valid", r.text
    finally:
        _cred_cleanup(engine, factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_put_ollama_credential_keyless(monkeypatch):
    """Ollama needs only a base URL (no API key). The credential endpoint must accept
    a keyless body for ollama and report the provider configured (F3)."""
    _use_test_key(monkeypatch)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            # Keyless ollama config with just a base URL succeeds and configures it.
            r = c.put(
                "/v1/providers/ollama/credentials",
                json={"base_url": "http://localhost:11434"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["configured"] is True

            got = c.get("/v1/model-config")
            prov = {p["provider"]: p for p in got.json()["providers"]}
            assert prov["ollama"]["configured"] is True

            # No key was stored (keyless): the M6 encrypted-credential guard must not trip.
            async def _stored_key():
                from sqlalchemy import select

                async with factory() as s:
                    row = (
                        (
                            await s.execute(
                                select(ProviderCredential).where(
                                    ProviderCredential.workspace_id == ws,
                                    ProviderCredential.provider == "ollama",
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return row.api_key_encrypted

            assert asyncio.run(_stored_key()) is None

            # A key-requiring provider still rejects a keyless body.
            bad = c.put("/v1/providers/openai/credentials", json={"base_url": "http://x"})
            assert bad.status_code == 400, bad.text
    finally:
        _cred_cleanup(engine, factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_rotating_a_key_preserves_base_url_and_extra_config(monkeypatch):
    """B1, the highest-value guard in this phase. The client sends only the field it
    changed; the server must not null the fields it did not receive."""
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            first = c.put(
                "/v1/providers/anthropic/credentials",
                json={
                    "api_key": "sk-original",
                    "base_url": "https://proxy.internal/v1",
                    "extra_config": {"org": "acme"},
                },
            )
            assert first.status_code == 200, first.text

            rotate = c.put("/v1/providers/anthropic/credentials", json={"api_key": "sk-rotated"})
            assert rotate.status_code == 200, rotate.text

            body = c.get("/v1/model-config").json()
            anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")
            assert anthropic["base_url"] == "https://proxy.internal/v1"
            assert "org" in anthropic["extra_config_secret_keys"]
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_empty_credential_body_does_not_reset_status(monkeypatch):
    """A no-op write must not invalidate a verification.

    The handler only ever writes status="untested" itself, so a prior "valid" state
    has to be seeded directly in the DB. PUT {} carries no fields at all -- it changes
    nothing -- and must leave that verification alone rather than downgrading it.
    """
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            created = c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-original"},
            )
            assert created.status_code == 200, created.text

            async def _mark_valid():
                from sqlalchemy import select

                async with factory() as s:
                    row = (
                        (
                            await s.execute(
                                select(ProviderCredential).where(
                                    ProviderCredential.workspace_id == ws,
                                    ProviderCredential.provider == "anthropic",
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    row.status = "valid"
                    await s.commit()

            asyncio.run(_mark_valid())

            empty = c.put("/v1/providers/anthropic/credentials", json={})
            assert empty.status_code == 200, empty.text

            body = c.get("/v1/model-config").json()
            anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")
            assert anthropic["status"] == "valid"
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_explicit_null_clears_base_url(monkeypatch):
    """Omitted means 'leave alone'; explicit null means 'clear'."""
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-x", "base_url": "https://proxy.internal/v1"},
            )
            c.put("/v1/providers/anthropic/credentials", json={"base_url": None})

            body = c.get("/v1/model-config").json()
            anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")
            assert anthropic["base_url"] is None
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_editing_base_url_alone_does_not_require_the_key(monkeypatch):
    """Once a credential exists, api_key is required only to CREATE one."""
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put("/v1/providers/anthropic/credentials", json={"api_key": "sk-x"})
            r = c.put(
                "/v1/providers/anthropic/credentials",
                json={"base_url": "https://new.proxy/v1"},
            )
            assert r.status_code == 200, r.text
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_ollama_stays_configured_after_a_keyless_write(monkeypatch):
    """base_url IS ollama's credential. Wiping it on save unconfigured the provider."""
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put(
                "/v1/providers/ollama/credentials",
                json={"base_url": "http://localhost:11434"},
            )
            c.put("/v1/providers/ollama/credentials", json={"extra_config": {"keep_alive": "5m"}})

            body = c.get("/v1/model-config").json()
            ollama = next(p for p in body["providers"] if p["provider"] == "ollama")
            assert ollama["configured"] is True
            assert ollama["base_url"] == "http://localhost:11434"
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_creating_a_credential_still_requires_a_key_for_keyed_providers(monkeypatch):
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            r = c.put("/v1/providers/openai/credentials", json={"base_url": "https://x/v1"})
            assert r.status_code == 400
            assert "api_key is required" in r.text
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_delete_credential_reports_what_it_breaks_and_still_deletes(monkeypatch):
    """Inform, do not block: a credential the founder cannot revoke is a security
    problem, not a safety feature."""
    _use_test_key(monkeypatch)
    # Deleting the stored credential only reads as "unconfigured" when there is no env
    # fallback behind it. `resolve_credential` consults settings last, so a developer with
    # MULDRO_OPENAI_API_KEY set would see the provider stay configured after the delete.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with pinned_deployment_defaults(factory), TestClient(app) as c:
            c.put("/v1/providers/openai/credentials", json={"api_key": "sk-x"})
            c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "fast",
                            "provider": "openai",
                            "model_id": "gpt-5-mini",
                            "effort": "low",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [],
                },
            )

            r = c.delete("/v1/providers/openai/credentials")
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["status"]["provider"] == "openai"
            assert body["status"]["configured"] is False
            orphaned = body["orphaned_bindings"]
            assert [w["scope_key"] for w in orphaned] == ["fast"]
            assert orphaned[0]["code"] == "provider_not_configured"
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_delete_reports_only_what_this_revoke_broke(monkeypatch):
    """orphaned_bindings must be scoped to the provider actually deleted.

    config.warnings spans every binding in the workspace, across all providers. If a
    workspace already has an unrelated provider unconfigured (here: a `balanced` tier
    bound to google_genai, which has no credential), deleting the OpenAI credential
    must not report that pre-existing, unrelated breakage as something THIS delete
    caused.
    """
    _use_test_key(monkeypatch)
    # Blank both env fallbacks so neither provider's warning is masked by a dev .env.
    # Do NOT blank anthropic_api_key -- validate_startup() hard-fails without it.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    monkeypatch.setattr(get_settings(), "google_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    async def _seed_broken_google_binding():
        async with factory() as db:
            db.add(
                ModelBinding(
                    workspace_id=ws,
                    scope_type="tier",
                    scope_key="balanced",
                    provider="google_genai",
                    model_id="gemini-2.5-pro",
                    effort="medium",
                    max_tokens=4096,
                    enabled=True,
                )
            )
            await db.commit()

    try:
        app = _ws_app(factory, ws)
        with pinned_deployment_defaults(factory), TestClient(app) as c:
            c.put("/v1/providers/openai/credentials", json={"api_key": "sk-x"})
            c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "fast",
                            "provider": "openai",
                            "model_id": "gpt-5-mini",
                            "effort": "low",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            asyncio.run(_seed_broken_google_binding())

            # Sanity: the pre-existing google_genai breakage is already visible before
            # the delete, and is not itself under test here.
            pre = c.get("/v1/model-config").json()
            assert any(w["scope_key"] == "balanced" for w in pre["warnings"])

            r = c.delete("/v1/providers/openai/credentials")
            assert r.status_code == 200, r.text
            body = r.json()

            orphaned = body["orphaned_bindings"]
            assert [w["scope_key"] for w in orphaned] == ["fast"]
            assert all(w["provider"] == "openai" for w in orphaned)
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


def test_merge_extra_config_is_three_valued():
    """The pure rule behind the extra_config merge, pinned without a database.

    extra_config carries SECRETS whose values a client can never read back, so the
    only thing a client can do with one is OMIT it. Omission must therefore mean
    "keep", or the form's "leave blank to keep" hint is a lie.
    """
    stored = {"region": "us-east-1", "secret_access_key": "shhh"}

    # Omitted key -> kept. The founder edited the region and could not resend the
    # secret; the secret survives.
    assert merge_extra_config(stored, {"region": "eu-west-1"}) == {
        "region": "eu-west-1",
        "secret_access_key": "shhh",
    }
    # Explicit null -> that key alone is deleted.
    assert merge_extra_config(stored, {"secret_access_key": None}) == {"region": "us-east-1"}
    # A new key joins the stored ones.
    assert merge_extra_config(stored, {"deployment": "gpt4o"})["deployment"] == "gpt4o"
    # Top-level explicit null still clears the whole map.
    assert merge_extra_config(stored, None) is None
    # Nothing stored yet: a null-valued key is dropped, not written as a JSON null.
    assert merge_extra_config(None, {"region": None}) is None
    assert merge_extra_config(None, {"region": "us-east-1"}) == {"region": "us-east-1"}
    # The stored dict is never mutated in place.
    assert stored == {"region": "us-east-1", "secret_access_key": "shhh"}


def _stored_extra_config(factory, ws: str, provider: str):
    async def _read():
        async with factory() as db:
            rows = await db.execute(
                select(ProviderCredential).where(
                    ProviderCredential.workspace_id == ws,
                    ProviderCredential.provider == provider,
                )
            )
            row = rows.scalars().first()
            return None if row is None else row.extra_config

    return asyncio.run(_read())


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_editing_a_public_extra_field_preserves_a_stored_extra_secret(monkeypatch):
    """B1 one level down, end to end.

    A Bedrock-shaped credential keeps its secret INSIDE extra_config. The client
    pre-fills the public fields, renders the secret blank ("configured -- leave blank
    to keep") and omits it on save. Replacing the map wholesale destroyed it,
    unrecoverably, against what the form had just promised.
    """
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            first = c.put(
                "/v1/providers/anthropic/credentials",
                json={
                    "api_key": "sk-original",
                    "extra_config": {"region": "us-east-1", "secret_access_key": "shhh"},
                },
            )
            assert first.status_code == 200, first.text

            # Only the region is edited; the secret is omitted, not resent.
            edit = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"region": "eu-west-1"}},
            )
            assert edit.status_code == 200, edit.text
            assert _stored_extra_config(factory, ws, "anthropic") == {
                "region": "eu-west-1",
                "secret_access_key": "shhh",
            }

            # An explicit null deletes one key without touching the rest.
            drop = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"secret_access_key": None}},
            )
            assert drop.status_code == 200, drop.text
            assert _stored_extra_config(factory, ws, "anthropic") == {"region": "eu-west-1"}

            # The secret value is never echoed on the way out; only its key name is public.
            assert "shhh" not in first.text + edit.text + drop.text
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)
