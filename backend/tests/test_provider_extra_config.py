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
from src.services.model_config_service import (
    MAX_EXTRA_VALUE_LEN,
    merge_extra_config,
    rejected_extra_keys,
)
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


def test_rejected_extra_keys_is_declaration_driven(monkeypatch):
    """The write-side twin of _split_extra_config's fail-closed read rule.

    The first block is the UNPATCHED baseline, and doubles as a leak canary: if
    _declare_extra_fields ever escaped its test, these would start returning {}.
    """
    # Nothing is declared for anthropic out of the box, so everything is unknown.
    assert rejected_extra_keys("anthropic", {"region": "us-east-1"}) == {
        "region": "not a declared field"
    }
    # api_key and base_url are top-level columns, never members of the map -- naming
    # one here is a SHADOW of the real field, not a legitimate extra.
    assert rejected_extra_keys("anthropic", {"base_url": "https://elsewhere.test/v1"}) == {
        "base_url": "not a declared field"
    }
    assert rejected_extra_keys("anthropic", None) == {}
    assert rejected_extra_keys("anthropic", {}) == {}

    _declare_extra_fields(monkeypatch)
    assert rejected_extra_keys("anthropic", {"region": "us-east-1", "org": "acme"}) == {}
    assert rejected_extra_keys("anthropic", {"region": "x", "zzz": 1, "aaa": 2}) == {
        "aaa": "not a declared field",
        "zzz": "not a declared field",
    }
    # Declaring a field does NOT make the top-level names legitimate map members.
    assert rejected_extra_keys("anthropic", {"api_key": "sk-x"}) == {
        "api_key": "not a declared field"
    }


def test_a_declared_key_still_has_to_carry_a_short_scalar(monkeypatch):
    """Validating NAMES alone let the write side accept what the read side refuses.

    _split_extra_config drops a non-scalar with a log warning and never echoes it, so
    a nested value stored under a declared key returned 200 and then rendered as a
    permanently blank field -- with only a server-side warning as evidence. The bytes
    axis matters too: per-key merge keeps whatever is written forever, and a blob
    under a SECRET key cannot be cleared through the form at all, because blank there
    means "keep".
    """
    _declare_extra_fields(monkeypatch)
    assert rejected_extra_keys("anthropic", {"region": {"deep": {"blob": "z"}}}) == {
        "region": "not a scalar"
    }
    assert rejected_extra_keys("anthropic", {"region": ["us-east-1"]}) == {"region": "not a scalar"}
    assert rejected_extra_keys("anthropic", {"org": "o" * (MAX_EXTRA_VALUE_LEN + 1)}) == {
        "org": f"longer than {MAX_EXTRA_VALUE_LEN} characters"
    }
    # Scalars the read side WILL echo are accepted, and so is an explicit null --
    # that is the delete verb, not a value.
    assert rejected_extra_keys("anthropic", {"region": "us-east-1", "org": None}) == {}
    assert rejected_extra_keys("anthropic", {"region": 1, "org": True}) == {}
    assert rejected_extra_keys("anthropic", {"org": "o" * MAX_EXTRA_VALUE_LEN}) == {}


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
    """Sibling of ``test_empty_credential_body_does_not_reset_status`` in
    ``test_routes_provider_credentials.py``, which pins the outer case (a body with
    no fields at all). This is the inner one, where per-key merge makes "changed
    nothing" non-trivial to detect. Both must hold; neither implies the other.

    `status` renders in the Providers tab, so downgrading a verified provider for a
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


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_a_declared_field_travels_the_whole_loop(monkeypatch):
    """Server declares a field -> client can render it -> client sends it -> server
    accepts it. That round trip is the entire point of a schema-driven credential
    form, and nothing exercised it: the catalog endpoint read a name bound at import
    time, so it could advertise one schema while the write gate enforced another.
    """
    _use_test_key(monkeypatch)
    _declare_extra_fields(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            catalog = c.get("/v1/model-catalog")
            assert catalog.status_code == 200, catalog.text
            spec = next(p for p in catalog.json()["providers"] if p["provider"] == "anthropic")
            declared = {f["key"]: f for f in spec["credential_fields"]}
            # The client renders from THIS, so the write gate must accept exactly it.
            assert declared["region"]["kind"] == "text"
            assert declared["org"]["kind"] == "secret"

            accepted = c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-x", "extra_config": {"region": "us-east-1"}},
            )
            assert accepted.status_code == 200, accepted.text
            # A non-secret declared field comes back for the form to pre-fill.
            assert accepted.json()["extra_config_public"] == {"region": "us-east-1"}
            # A secret one is named, never valued.
            stored = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"org": "acme"}},
            )
            assert stored.json()["extra_config_secret_keys"] == ["org"]
            assert "acme" not in stored.text
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_the_rejection_detail_is_bounded_and_structural_errors_come_first(monkeypatch):
    """The detail is echoed to the client AND logged, so it is sized for a human.

    Unbounded, a body of long junk keys reflected its own payload back amplified --
    a multi-megabyte 400 and an equally large log line.
    """
    _use_test_key(monkeypatch)
    _declare_extra_fields(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    try:
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            junk = c.put(
                "/v1/providers/anthropic/credentials",
                json={"api_key": "sk-x", "extra_config": {f"k{i}" * 200: 1 for i in range(50)}},
            )
            assert junk.status_code == 400, junk.text
            # Envelope is {"error": {"code", "message", "correlation_id"}}.
            detail = junk.json()["error"]["message"]
            assert len(detail) < 1000, len(detail)
            assert "and 45 more" in detail

            # Ordering: a create missing its api_key AND carrying junk reports the
            # STRUCTURAL error, which is the one the client cannot guess. Reporting
            # the guessable one first means fixing it only surfaces a second 400.
            both = c.put(
                "/v1/providers/anthropic/credentials",
                json={"extra_config": {"nope": "x"}},
            )
            assert both.status_code == 400, both.text
            assert "nope" in both.json()["error"]["message"]
            assert "api_key is required" not in both.json()["error"]["message"]
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
def test_an_empty_stored_map_is_not_a_change(monkeypatch):
    """The one input where change-detection was inexact.

    merge_extra_config collapses an emptied map to None, so a row seeded with {}
    rather than NULL -- reachable only by direct DB insert -- read as changed by a
    write that touched nothing, and downgraded a verified provider.
    """
    _use_test_key(monkeypatch)
    _declare_extra_fields(monkeypatch)
    factory, ws = _ws_factory()
    app = None

    async def _seed_empty_map():
        async with factory() as db:
            db.add(
                ProviderCredential(
                    workspace_id=ws,
                    provider="anthropic",
                    api_key_encrypted=None,
                    extra_config={},
                    status="valid",
                    enabled=True,
                )
            )
            await db.commit()

    try:
        asyncio.run(_seed_empty_map())
        app = _ws_app(factory, ws)
        with TestClient(app) as c:
            noop = c.put("/v1/providers/anthropic/credentials", json={"extra_config": {}})
            assert noop.status_code == 200, noop.text
            assert noop.json()["status"] == "valid"
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        _delete_ws_credentials(factory, ws)
