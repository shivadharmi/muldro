"""Tests for the extra_config half of a provider credential.

Split from ``test_routes_provider_credentials.py`` because it is one coherent rule
with two sides that must agree: ``merge_extra_config`` decides what is STORED and
``_split_extra_config`` decides what is RETURNED, and the map holds secrets whose
values a client can never read back. That asymmetry -- the client can only OMIT a
secret -- is what forces per-key merge, declaration-checked writes, and a
change-detecting status downgrade, all of which live here.

No shipped provider declares an extra_config field yet (Bedrock and Azure are the
reason the credential form is schema-driven and neither has landed), so these tests
declare the schema they exercise via ``_declare_extra_fields``.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.models.provider_credential import ProviderCredential
from src.services.model_config_service import merge_extra_config, unknown_extra_keys
from tests.helpers.model_config import (
    _db_reachable,
    _declare_extra_fields,
    _delete_ws_credentials,
    _use_test_key,
    _ws_app,
    _ws_factory,
)


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
    _declare_extra_fields(monkeypatch)
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


def test_unknown_extra_keys_is_declaration_driven(monkeypatch):
    """The write-side twin of _split_extra_config's fail-closed read rule."""
    # Nothing is declared for anthropic out of the box, so everything is unknown.
    assert unknown_extra_keys("anthropic", {"region": "us-east-1"}) == ["region"]
    # api_key and base_url are top-level columns, never members of the map -- naming
    # one here is a SHADOW of the real field, not a legitimate extra.
    assert unknown_extra_keys("anthropic", {"base_url": "https://elsewhere.test/v1"}) == [
        "base_url"
    ]
    assert unknown_extra_keys("anthropic", None) == []
    assert unknown_extra_keys("anthropic", {}) == []

    _declare_extra_fields(monkeypatch)
    assert unknown_extra_keys("anthropic", {"region": "us-east-1", "org": "acme"}) == []
    assert unknown_extra_keys("anthropic", {"region": "x", "zzz": 1, "aaa": 2}) == ["aaa", "zzz"]
    # Declaring a field does NOT make the top-level names legitimate map members.
    assert unknown_extra_keys("anthropic", {"api_key": "sk-x"}) == ["api_key"]


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_an_undeclared_extra_key_is_rejected_and_stores_nothing(monkeypatch):
    """A merge is accumulative, so a key written once would live in the JSONB forever,
    invisible to the form and undeletable through it. It is refused on write instead --
    which is safe against catalog drift precisely BECAUSE omission now means keep, so
    refusing a write can never destroy what is already stored.
    """
    _use_test_key(monkeypatch)
    _declare_extra_fields(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-x", "extra_config": {"region": "us-east-1"}},
            )

            junk = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"zzz": "padding", "region": "eu-west-1"}},
            )
            assert junk.status_code == 400, junk.text
            assert "zzz" in junk.text

            # Rejected whole: the legitimate key in the same body was not applied either.
            assert _stored_extra_config(factory, ws, "anthropic") == {"region": "us-east-1"}

            # A key shadowing a top-level field would be echoed back as public and
            # contradict the real base_url that describes the same thing.
            shadow = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"base_url": "https://elsewhere.test/v1"}},
            )
            assert shadow.status_code == 400, shadow.text
            assert _stored_extra_config(factory, ws, "anthropic") == {"region": "us-east-1"}
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_an_extra_config_write_that_changes_nothing_does_not_reset_status(monkeypatch):
    """`status` renders in the Providers tab, so downgrading a verified provider for a
    request that wrote nothing is a visible lie. Under a merge, presence of the field
    is no longer proof of a change: an empty map merges to what was already there.
    """
    _use_test_key(monkeypatch)
    _declare_extra_fields(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-x", "extra_config": {"region": "us-east-1"}},
            )

            async def _mark_valid():
                async with factory() as db:
                    rows = await db.execute(
                        select(ProviderCredential).where(
                            ProviderCredential.workspace_id == ws,
                            ProviderCredential.provider == "anthropic",
                        )
                    )
                    rows.scalars().first().status = "valid"
                    await db.commit()

            asyncio.run(_mark_valid())

            noop = c.put("/v1/providers/anthropic/credentials", json={"extra_config": {}})
            assert noop.status_code == 200, noop.text
            assert noop.json()["status"] == "valid"

            # Re-sending the SAME value is equally a no-op.
            same = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"region": "us-east-1"}},
            )
            assert same.json()["status"] == "valid"

            # A real change still invalidates the verification.
            real = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"region": "eu-west-1"}},
            )
            assert real.json()["status"] == "untested"
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)
