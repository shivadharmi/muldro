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
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config import secret_crypto
from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.models.users import User, Workspace
from src.services.model_config_service import ModelConfigService
from src.services.model_resolver import ModelResolver


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


async def test_undecryptable_credential_reads_as_unconfigured_not_a_crash(monkeypatch):
    """A rotated MULDRO_CONFIG_ENCRYPTION_KEY leaves stored ciphertexts undecryptable.

    Before this fix, resolve_credential propagated the raw Fernet error straight out of
    decrypt_secret -- turning GET /v1/model-config into a 500 for every provider with a
    now-undecryptable row, including the one page that could delete the bad row. It must
    instead fall through to the env fallback and treat the row as unusable.

    A VALID current master key is set first, so the stored ciphertext fails to decrypt
    via ``InvalidToken`` (one row unusable under a known-good key -- the rotated-key
    scenario this test is named for), not via the missing-key ``RuntimeError`` that a
    genuinely unset key produces. Those are different failure modes: FIX A keeps the
    second loud (see ``test_a_missing_master_key_still_raises`` below); this test covers
    only the first.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    # resolve_credential consults the env fallback last, and this worktree's .env has
    # MULDRO_OPENAI_API_KEY set -- blank it so the env key cannot mask the result.
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)

    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=ws,
                provider="openai",
                api_key_encrypted="not-valid-fernet-ciphertext",
            )
        )
        await db.flush()

        api_key, _base_url = await ModelResolver(db).resolve_credential("openai", ws)
        assert api_key is None


async def test_undecryptable_row_does_not_lend_its_base_url_to_the_env_key(monkeypatch):
    """SECURITY: a row that fails to decrypt must not lend its base_url to the env key.

    A workspace can configure its own key AND a custom base_url (anthropic, openai and
    google_genai all declare an optional base_url in provider_catalog.py, and all three
    have env fallbacks). If the master key is later rotated and that row stops
    decrypting, pairing the fallthrough's DEPLOYMENT-wide env key with THIS row's
    workspace-chosen base_url would send the shared credential to an endpoint it was
    never configured against -- a routine key rotation turning into exfiltration of the
    shared key to every workspace-configured custom URL. The row must be discarded
    whole, base_url included, once it is known unusable.

    Uses a valid (test) master key so the row's ciphertext fails via InvalidToken --
    the "one row is corrupt" case FIX A carves out -- rather than via the missing-key
    RuntimeError, which is a different failure mode covered separately below.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    monkeypatch.setattr(get_settings(), "openai_api_key", "env-key", raising=False)

    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=ws,
                provider="openai",
                api_key_encrypted="not-valid-fernet-ciphertext",
                base_url="https://workspace-chosen.example/v1",
            )
        )
        await db.flush()

        result = await ModelResolver(db).resolve_credential("openai", ws)
        assert result == ("env-key", None)


async def test_provider_status_reports_an_undecryptable_row_as_unconfigured(monkeypatch):
    """FIX C: ``configured`` must agree with what the runtime can actually use.

    Before this fix, ``_provider_statuses`` computed ``has_material`` from column
    presence alone (``bool(cred.api_key_encrypted)``), never attempting decryption. A
    row whose ciphertext no longer decrypts (e.g. after a master-key rotation) would
    therefore read as ``configured: true`` with a stale ``status`` (here "valid"),
    directly contradicting the runtime, which cannot use it. This is the gap FIX C
    closes, previously untested against GET /v1/model-config's ``providers`` array.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)

    async with _session() as db:
        ws = await _seed_workspace(db)
        db.add(
            ProviderCredential(
                workspace_id=ws,
                provider="openai",
                api_key_encrypted="not-valid-fernet-ciphertext",
                status="valid",
            )
        )
        await db.flush()

        statuses = await ModelConfigService(db).provider_statuses(ws)
        openai = next(s for s in statuses if s.provider == "openai")
        assert openai.configured is False


def test_a_missing_master_key_still_raises(monkeypatch):
    """FIX A: a deployment-wide misconfiguration (no master key) must keep
    propagating loudly, not be swallowed as though it were one corrupt row.

    ``src/api/app.py``'s §4.3 boot guard documents the turn-time RuntimeError from
    ``secret_crypto._fernet()`` as its own backstop for when the guard's DB check
    can't run (DB unreachable at startup). Only ``InvalidToken`` -- ONE ciphertext
    unusable under a KNOWN-valid key -- may return None from ``try_decrypt_secret``.
    """
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: "")
    with pytest.raises(RuntimeError, match="MULDRO_CONFIG_ENCRYPTION_KEY"):
        secret_crypto.try_decrypt_secret("anything")
