"""Tests for PUT/GET /v1/model-config: binding CRUD, scope_type/effort
validation, and agent-override replace semantics.

Provider-configuration-state-driven behaviour (provider-status shape, warnings,
bind-time rejection against an unconfigured provider, the uncatalogued-provider
case, and the section-2.5 no-fallback regression guard) lives in
`test_routes_model_config_provider_state.py` -- this file was split by
responsibility once it grew past the 800-line cap.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.app import create_app
from src.api.deps import (
    get_current_user,
    get_current_user_id,
    get_current_workspace_id,
    get_session,
)
from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from tests.conftest import TEST_USER_ID
from tests.helpers.model_config import (
    _client,
    _cred_app,
    _db_reachable,
    _seed_ws,
    _ws_app,
    _ws_factory,
    pinned_deployment_defaults,
)


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
        with pinned_deployment_defaults(factory), TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
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
            # The planner override is re-sent because agent overrides use replace
            # semantics — an omitted override would be pruned (see the F2 test below).
            put2 = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "low",
                            "max_tokens": 2048,
                        }
                    ],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put2.status_code == 200, put2.text

            got = c.get("/v1/model-config")
            assert got.status_code == 200, got.text
            body = got.json()

            tiers = {t["scope_key"]: t for t in body["tiers"]}
            # Workspace override wins for balanced (re-PUT effort=low).
            assert tiers["balanced"]["model_id"] == "claude-sonnet-4-6"
            assert tiers["balanced"]["effort"] == "low"
            assert tiers["balanced"]["max_tokens"] == 2048
            # Untouched tiers fall through to the deployment defaults.
            assert tiers["reasoning"]["model_id"] == "claude-opus-4-8"
            assert tiers["fast"]["model_id"] == "claude-haiku-4-5-20251001"

            # Agent override round-trips with the agent name in scope_key.
            overrides = {o["scope_key"]: o for o in body["agent_overrides"]}
            assert overrides["planner"]["model_id"] == "claude-opus-4-8"

            # Provider statuses cover the whole catalog. Anthropic has no
            # credential row here, but the deployment env key (MULDRO_ANTHROPIC_API_KEY)
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
                            "scope_type": "tier",
                            "scope_key": "balanced",
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


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_put_config_removes_omitted_agent_overrides():
    """Re-PUTing model-config with a shorter agent_overrides list must delete the
    workspace override rows omitted from the submission — replace semantics (F2)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws = asyncio.run(_seed_ws(factory))
    app = _cred_app(ws, factory)
    try:
        with TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        },
                        {
                            "scope_type": "agent",
                            "scope_key": "presenter",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        },
                    ],
                },
            )
            assert put.status_code == 200, put.text
            overrides = {o["scope_key"] for o in put.json()["agent_overrides"]}
            assert overrides == {"planner", "presenter"}

            # Re-PUT dropping presenter -> it must disappear (replace, not merge).
            put2 = c.put(
                "/v1/model-config",
                json={
                    "tiers": [],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put2.status_code == 200, put2.text
            assert {o["scope_key"] for o in put2.json()["agent_overrides"]} == {"planner"}

            # Empty list clears all remaining overrides.
            put3 = c.put(
                "/v1/model-config",
                json={"tiers": [], "agent_overrides": []},
            )
            assert put3.status_code == 200, put3.text
            assert put3.json()["agent_overrides"] == []
    finally:

        async def _cleanup():
            async with factory() as s:
                await s.execute(delete(ModelBinding).where(ModelBinding.workspace_id == ws))
                await s.commit()
            await engine.dispose()

        asyncio.run(_cleanup())


def test_put_config_rejects_zero_max_tokens():
    """max_tokens=0 must be rejected — it produces a legacy thinking budget of -1 and
    breaks every model call on that binding (N1). Validation is at the schema boundary,
    so no DB is needed."""
    with _client() as c:
        for bad in (0, -5):
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": bad,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert r.status_code == 422, f"max_tokens={bad} should be rejected: {r.text}"


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_binding_dto_round_trips_scope_type():
    """An agent override named after a tier stays distinct from the tier itself —
    the contract no longer recovers scope from which array it arrived in."""
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)

    try:
        with TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    # Deliberately named after a TIER. The DB stores (scope_type,
                    # scope_key), so this must round-trip as an agent override and
                    # never merge with the "balanced" tier above.
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put.status_code == 200, put.text
            body = put.json()

            tier = next(b for b in body["tiers"] if b["scope_key"] == "balanced")
            assert tier["scope_type"] == "tier"
            assert tier["model_id"] == "claude-sonnet-4-6"

            override = next(b for b in body["agent_overrides"] if b["scope_key"] == "balanced")
            assert override["scope_type"] == "agent"
            assert override["model_id"] == "claude-opus-4-8"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_binding_rejects_effort_outside_the_literal():
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)
    try:
        with TestClient(app) as c:
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "extreme",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_binding_rejects_scope_type_that_contradicts_its_list():
    """A tiers[] entry declaring scope_type='agent' would write a tier row from an
    agent DTO. Reject it rather than silently coercing."""
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)
    try:
        with TestClient(app) as c:
            r = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "agent",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [],
                },
            )
            assert r.status_code == 422
            assert "scope_type" in r.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_omitting_agent_overrides_preserves_them():
    """A tiers-only PUT that omits agent_overrides entirely must NOT delete existing
    overrides. agent_overrides defaults to [] on the wire, and overrides use REPLACE
    semantics, so collapsing 'the key was absent' into 'the key was an empty list'
    would let a client wipe every override in the workspace just by PUTing tiers."""
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)
    try:
        with TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put.status_code == 200, put.text
            assert {o["scope_key"] for o in put.json()["agent_overrides"]} == {"planner"}

            # Re-PUT with only "tiers" in the JSON body -- agent_overrides key absent.
            put2 = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "low",
                            "max_tokens": 2048,
                        }
                    ]
                },
            )
            assert put2.status_code == 200, put2.text

            got = c.get("/v1/model-config")
            assert got.status_code == 200, got.text
            assert {o["scope_key"] for o in got.json()["agent_overrides"]} == {"planner"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_explicit_empty_agent_overrides_clears_them():
    """An explicit agent_overrides=[] still means 'clear them all' -- keeps REPLACE
    semantics honest against the None-means-absent change above."""
    factory, ws = _ws_factory()
    app = _ws_app(factory, ws)
    try:
        with TestClient(app) as c:
            put = c.put(
                "/v1/model-config",
                json={
                    "tiers": [
                        {
                            "scope_type": "tier",
                            "scope_key": "balanced",
                            "provider": "anthropic",
                            "model_id": "claude-sonnet-4-6",
                            "effort": "medium",
                            "max_tokens": 4096,
                        }
                    ],
                    "agent_overrides": [
                        {
                            "scope_type": "agent",
                            "scope_key": "planner",
                            "provider": "anthropic",
                            "model_id": "claude-opus-4-8",
                            "effort": "high",
                            "max_tokens": 8192,
                        }
                    ],
                },
            )
            assert put.status_code == 200, put.text
            assert {o["scope_key"] for o in put.json()["agent_overrides"]} == {"planner"}

            put2 = c.put(
                "/v1/model-config",
                json={"tiers": [], "agent_overrides": []},
            )
            assert put2.status_code == 200, put2.text
            assert put2.json()["agent_overrides"] == []

            got = c.get("/v1/model-config")
            assert got.status_code == 200, got.text
            assert got.json()["agent_overrides"] == []
    finally:
        app.dependency_overrides.clear()
