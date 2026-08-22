"""Tests for /v1/model-config behaviour that is driven by provider
credential state: provider-status shape, warnings, bind-time rejection
against an unconfigured provider (Task 8), the uncatalogued-provider case,
and the section-2.5 no-fallback regression guard.

Split out of `test_routes_model_config.py` (which keeps the binding CRUD and
scope_type/effort validation tests) once that file grew past the 800-line cap.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from tests.helpers.model_config import (
    _db_reachable,
    _delete_ws_credentials,
    _use_test_key,
    _ws_app,
    _ws_factory,
)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_provider_status_exposes_base_url_but_never_a_secret(monkeypatch):
    """B2: a client cannot preserve what it cannot see. Non-secret values come back;
    secret ones come back as key names only.

    Sets a throwaway master key before app startup: create_app()'s §4.3 guard
    (src/api/app.py) fails loud if ANY provider_credentials row in the DB has a
    non-null api_key_encrypted while MULDRO_CONFIG_ENCRYPTION_KEY is unset -- and
    this test seeds exactly such a row before the app boots. GET /v1/model-config
    now also computes `warnings` (Task 7), which calls ModelResolver.resolve_credential
    -- the same call the runtime makes -- for every tier/agent binding, and that
    decrypts a stored credential rather than merely reading its `status`. So the
    seeded ciphertext must actually decrypt under this key, not just be present:
    a real Fernet key is used for both the settings override and the ciphertext.
    The row is deleted in `finally` so it can't trip the guard for any test that
    creates an app afterwards.
    """
    from cryptography.fernet import Fernet

    fernet_key = Fernet.generate_key()
    monkeypatch.setattr(get_settings(), "config_encryption_key", fernet_key.decode())
    factory, ws = _ws_factory()
    app = None

    async def _seed_cred():
        async with factory() as db:
            db.add(
                ProviderCredential(
                    workspace_id=ws,
                    provider="anthropic",
                    api_key_encrypted=Fernet(fernet_key).encrypt(b"sk-decoy").decode(),
                    base_url="https://proxy.internal/v1",
                    extra_config={"api_key": "leaked-if-echoed", "region": "eu-west-1"},
                    status="valid",
                    enabled=True,
                )
            )
            await db.commit()

    try:
        asyncio.run(_seed_cred())
        app = _ws_app(factory, ws)

        with TestClient(app) as c:
            body = c.get("/v1/model-config").json()
            anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")

            assert anthropic["base_url"] == "https://proxy.internal/v1"
            # api_key is a declared secret field -> name only.
            assert "api_key" in anthropic["extra_config_secret_keys"]
            assert "api_key" not in anthropic["extra_config_public"]
            # region is NOT a declared field for anthropic -> fails closed, hidden too.
            assert "region" in anthropic["extra_config_secret_keys"]
            assert anthropic["extra_config_public"] == {}
            assert "leaked-if-echoed" not in c.get("/v1/model-config").text
    finally:
        if app is not None:
            app.dependency_overrides.clear()

        async def _cleanup():
            async with factory() as db:
                await db.execute(
                    delete(ProviderCredential).where(ProviderCredential.workspace_id == ws)
                )
                await db.commit()

        asyncio.run(_cleanup())


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_null_extra_config_value_is_omitted_not_stringified():
    """A stored JSON null must not become the literal string "None".

    ``extra_config`` is untyped JSONB, so a stored ``null`` is a real possibility
    for any declared-public field. ``str(None)`` would pre-fill the credential
    form's text box with the four-character word "None" -- a value the user never
    typed -- which then round-trips back as a real value on the next Save. The
    key must be omitted entirely instead.
    """
    factory, ws = _ws_factory()
    app = None

    async def _seed_cred():
        async with factory() as db:
            db.add(
                ProviderCredential(
                    workspace_id=ws,
                    provider="anthropic",
                    base_url=None,
                    extra_config={"base_url": None},
                    status="valid",
                    enabled=True,
                )
            )
            await db.commit()

    try:
        asyncio.run(_seed_cred())
        app = _ws_app(factory, ws)

        with TestClient(app) as c:
            body = c.get("/v1/model-config").json()
            anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")

            assert "base_url" not in anthropic["extra_config_public"]
            assert anthropic["extra_config_public"].get("base_url") != "None"
    finally:
        if app is not None:
            app.dependency_overrides.clear()

        async def _cleanup():
            async with factory() as db:
                await db.execute(
                    delete(ProviderCredential).where(ProviderCredential.workspace_id == ws)
                )
                await db.commit()

        asyncio.run(_cleanup())


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_provider_without_a_credential_returns_empty_collections_not_null():
    """The client indexes these directly; null would crash the Providers list."""
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)
    try:
        with TestClient(app) as c:
            body = c.get("/v1/model-config").json()
            for p in body["providers"]:
                assert isinstance(p["extra_config_public"], dict)
                assert isinstance(p["extra_config_secret_keys"], list)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_credential_for_uncatalogued_provider_is_still_visible(monkeypatch):
    """B6: _provider_statuses iterated MODEL_CATALOG, so a row for a provider that
    was removed from the catalog became invisible and unmanageable.

    Sets a throwaway master key before app startup: create_app()'s §4.3 guard
    (src/api/app.py) fails loud if ANY provider_credentials row in the DB has a
    non-null api_key_encrypted while MULDRO_CONFIG_ENCRYPTION_KEY is unset -- and
    this test seeds exactly such a row before the app boots. The row is deleted in
    `finally` so it can't trip the same guard for any test that creates an app
    afterwards.

    The key must be a REAL Fernet key, not just a truthy string: GET /v1/model-config
    now decrypts every row's ciphertext to compute `configured` (FIX C), and a
    malformed key raises ValueError out of Fernet's own constructor instead of the
    per-row InvalidToken that path is designed to absorb. `_use_test_key` covers the
    decrypt path; `config_encryption_key` is set separately (same real key) because
    that is the literal attribute the boot guard's truthiness check reads.
    """
    from cryptography.fernet import Fernet

    _use_test_key(monkeypatch)
    monkeypatch.setattr(get_settings(), "config_encryption_key", Fernet.generate_key().decode())
    factory, ws = _ws_factory()
    app = None

    async def _seed_cred():
        async with factory() as db:
            db.add(
                ProviderCredential(
                    workspace_id=ws,
                    provider="retired_vendor",
                    api_key_encrypted="ciphertext",
                    status="valid",
                    enabled=True,
                )
            )
            await db.commit()

    try:
        asyncio.run(_seed_cred())
        app = _ws_app(factory, ws)

        with TestClient(app) as c:
            body = c.get("/v1/model-config").json()
            names = [p["provider"] for p in body["providers"]]
            assert "retired_vendor" in names
            # Catalogued providers keep their order; strays are appended.
            assert names.index("anthropic") < names.index("retired_vendor")
            by_provider = {p["provider"]: p for p in body["providers"]}
            assert by_provider["retired_vendor"]["catalogued"] is False
            assert by_provider["anthropic"]["catalogued"] is True
    finally:
        if app is not None:
            app.dependency_overrides.clear()

        async def _cleanup():
            async with factory() as db:
                await db.execute(
                    delete(ProviderCredential).where(
                        ProviderCredential.workspace_id == ws,
                        ProviderCredential.provider == "retired_vendor",
                    )
                )
                await db.commit()

        asyncio.run(_cleanup())


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_warning_appears_when_a_credential_is_revoked_after_binding(monkeypatch):
    """The path save-time validation cannot see: the binding was valid when saved."""
    _use_test_key(monkeypatch)
    # Deleting the stored credential only reads as "unconfigured" when there is no env
    # fallback behind it. `resolve_credential` consults settings last, so a developer with
    # MULDRO_OPENAI_API_KEY set would see the provider stay configured after the delete.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put("/v1/providers/openai/credentials", json={"api_key": "sk-x"})
            put = c.put(
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
            assert put.status_code == 200, put.text
            assert put.json()["warnings"] == []

            c.delete("/v1/providers/openai/credentials")

            body = c.get("/v1/model-config").json()
            warning = next(w for w in body["warnings"] if w["scope_key"] == "fast")
            assert warning["scope_type"] == "tier"
            assert warning["code"] == "provider_not_configured"
            assert "no tier fallback" in warning["message"]
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_agent_override_warning_says_it_degrades(monkeypatch):
    """An agent override DOES fall back to its tier binding (model_resolver.py:70),
    so its warning must not claim the same consequence a tier's does."""
    _use_test_key(monkeypatch)
    # Deleting the stored credential only reads as "unconfigured" when there is no env
    # fallback behind it. `resolve_credential` consults settings last, so a developer with
    # MULDRO_OPENAI_API_KEY set would see the provider stay configured after the delete.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put("/v1/providers/openai/credentials", json={"api_key": "sk-x"})
            c.put(
                "/v1/model-config",
                json={
                    "tiers": [],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "openai",
                            "model_id": "gpt-5",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            c.delete("/v1/providers/openai/credentials")

            body = c.get("/v1/model-config").json()
            warning = next(w for w in body["warnings"] if w["scope_key"] == "planner")
            assert warning["scope_type"] == "agent"
            assert "fall back to its tier binding" in warning["message"]
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_deployment_default_binding_warns_without_passing_through_put_config(monkeypatch):
    """The seed path. A NULL-workspace binding is created at startup and never goes
    near put_config, so bind-time validation can never see it.

    This test writes a NULL-workspace row, which is shared across the whole
    deployment -- it drops the row before and after itself so a failed run cannot
    poison the dev DB. ModelConfigRegistry.seed_defaults() has typically already
    populated a real NULL-workspace "reasoning" row (the unique index is on
    (scope_type, scope_key) alone, not provider -- see uq_model_binding_default in
    src/models/model_binding.py), so any pre-existing row is saved and restored
    rather than merely deleted.
    """
    # Same trap as the other two warning tests: `resolve_credential` consults the
    # env fallback last, and this worktree's .env has MULDRO_OPENAI_API_KEY set --
    # without clearing it the seeded binding would still resolve and produce no
    # warning.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()

    async def _existing_default():
        from sqlalchemy import select

        async with factory() as db:
            row = (
                (
                    await db.execute(
                        select(ModelBinding).where(
                            ModelBinding.workspace_id.is_(None),
                            ModelBinding.scope_type == "tier",
                            ModelBinding.scope_key == "reasoning",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            return {
                "provider": row.provider,
                "model_id": row.model_id,
                "effort": row.effort,
                "max_tokens": row.max_tokens,
                "temperature": row.temperature,
            }

    async def _drop_default_binding():
        async with factory() as db:
            await db.execute(
                delete(ModelBinding).where(
                    ModelBinding.workspace_id.is_(None),
                    ModelBinding.scope_type == "tier",
                    ModelBinding.scope_key == "reasoning",
                )
            )
            await db.commit()

    async def _seed_default_binding():
        async with factory() as db:
            db.add(
                ModelBinding(
                    workspace_id=None,
                    scope_type="tier",
                    scope_key="reasoning",
                    provider="openai",
                    model_id="gpt-5",
                    effort="high",
                    max_tokens=8192,
                    enabled=True,
                )
            )
            await db.commit()

    async def _restore_default_binding(saved: dict) -> None:
        async with factory() as db:
            db.add(
                ModelBinding(
                    workspace_id=None,
                    scope_type="tier",
                    scope_key="reasoning",
                    provider=saved["provider"],
                    model_id=saved["model_id"],
                    effort=saved["effort"],
                    max_tokens=saved["max_tokens"],
                    temperature=saved["temperature"],
                    enabled=True,
                )
            )
            await db.commit()

    saved = asyncio.run(_existing_default())
    asyncio.run(_drop_default_binding())
    app = None

    try:
        asyncio.run(_seed_default_binding())
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            body = c.get("/v1/model-config").json()
            assert any(
                w["scope_key"] == "reasoning" and w["code"] == "provider_not_configured"
                for w in body["warnings"]
            )
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        asyncio.run(_drop_default_binding())
        if saved is not None:
            asyncio.run(_restore_default_binding(saved))


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_clearing_a_key_reports_the_provider_unconfigured(monkeypatch):
    """PUT {"api_key": null} clears the stored key but leaves the row in place.

    ``configured`` must mean what ModelResolver means -- usable credential material,
    not merely a row -- so a cleared key must read as unconfigured, not as still
    configured because a ProviderCredential row still exists.
    """
    _use_test_key(monkeypatch)
    # resolve_credential consults the env fallback last, and this worktree's .env has
    # MULDRO_OPENAI_API_KEY set -- blank it so the env key cannot mask the result.
    # (anthropic_api_key can't be used for this trick: validate_startup() requires it
    # to be set, so blanking it before the app boots would break TestClient startup.)
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            created = c.put(
                "/v1/providers/openai/credentials",
                json={"api_key": "sk-original"},
            )
            assert created.status_code == 200, created.text

            cleared = c.put("/v1/providers/openai/credentials", json={"api_key": None})
            assert cleared.status_code == 200, cleared.text

            body = c.get("/v1/model-config").json()
            openai = next(p for p in body["providers"] if p["provider"] == "openai")
            assert openai["configured"] is False
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_keyless_provider_with_a_base_url_is_still_configured(monkeypatch):
    """Pins that the has_material fix does not break keyless providers.

    ollama has no api_key_encrypted -- base_url IS its credential -- so it must stay
    configured even though it never has key material.
    """
    _use_test_key(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            r = c.put(
                "/v1/providers/ollama/credentials",
                json={"base_url": "http://localhost:11434"},
            )
            assert r.status_code == 200, r.text

            body = c.get("/v1/model-config").json()
            ollama = next(p for p in body["providers"] if p["provider"] == "ollama")
            assert ollama["configured"] is True
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_binding_a_tier_to_an_unconfigured_provider_is_rejected(monkeypatch):
    """B3, bind path. Saving this used to succeed and then fail every run."""
    # resolve_credential consults the env fallback last, and this worktree's .env has
    # MULDRO_OPENAI_API_KEY set -- blank it so the env key cannot mask the result.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "reasoning",
                            "provider": "openai",
                            "model_id": "gpt-5",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert detail[0]["scope_key"] == "reasoning"
            assert detail[0]["code"] == "provider_not_configured"
    finally:
        if app is not None:
            app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_binding_an_agent_override_to_an_unconfigured_provider_is_allowed(monkeypatch):
    """An override degrades to its tier binding, so rejecting it would be stricter
    than the runtime. It warns instead."""
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "openai",
                            "model_id": "gpt-5",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert r.status_code == 200, r.text
            assert any(w["scope_key"] == "planner" for w in r.json()["warnings"])
    finally:
        if app is not None:
            app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_keyless_provider_survives_bind_validation():
    """KEYLESS_PROVIDERS must be honoured, or the new reject breaks local models."""
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "ollama",
                            "model_id": "llama3.1",
                            "effort": "none",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert r.status_code == 200, r.text
    finally:
        if app is not None:
            app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_tier_binding_with_no_credential_raises_and_does_not_fall_back(monkeypatch):
    """Spec §2.5. A workspace tier binding whose provider is unusable must RAISE.
    _pick_binding prefers the workspace row over the deployment-default row by
    EXISTENCE, and must not start skipping it on usability."""
    import pytest as _pytest

    from src.services.model_resolver import ModelConfigError, ModelResolver

    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
    factory, ws = _ws_factory()

    async def _seed_and_resolve():
        async with factory() as db:
            db.add(
                ModelBinding(
                    workspace_id=ws,
                    scope_type="tier",
                    scope_key="reasoning",
                    provider="openai",
                    model_id="gpt-5",
                    effort="high",
                    max_tokens=8192,
                    enabled=True,
                )
            )
            await db.commit()
        async with factory() as db:
            with _pytest.raises(ModelConfigError) as excinfo:
                await ModelResolver(db).resolve(tier="reasoning", workspace_id=ws)
            return excinfo.value

    err = asyncio.run(_seed_and_resolve())
    assert err.provider == "openai"
    assert err.scope_type == "tier"
    assert err.scope_key == "reasoning"
    assert "Settings" in (err.remediation or "")
