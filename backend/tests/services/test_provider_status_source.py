"""``ProviderStatus.source`` names WHERE a credential comes from.

``configured`` is true for three different sources — this workspace's own
``ProviderCredential`` row, the NULL-workspace deployment-default row, and the
per-provider env fallback key — but ``DELETE /v1/providers/{p}/credentials``
removes only the first. A client that gates its Remove control on ``configured``
therefore offers a button that silently does nothing for the other two, then
re-renders the provider as configured on the next refetch.

Pure unit tests: ``_provider_statuses`` is classification logic over rows, so the
DB session is a stub rather than a real one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services.model_config_service import ModelConfigService


def _db_returning(rows: list) -> MagicMock:
    """A stub session whose single ``execute`` yields *rows* via ``.scalars().all()``."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _cred(provider: str, workspace_id: str | None, status: str = "valid") -> SimpleNamespace:
    # base_url/extra_config mirror ProviderCredential's nullable columns (both default
    # None on a real row) so this stub keeps matching _provider_statuses's attribute
    # access as it grows -- these tests assert on source/configured only.
    #
    # api_key_encrypted defaults to a stand-in ciphertext: every _cred() in this file
    # represents a real, previously-PUT credential (per the module docstring -- "this
    # workspace's own ProviderCredential row"), which always has key material. A
    # cleared-key row (api_key_encrypted=None) is a different fixture, exercised by
    # ModelConfigService's own has_material tests, not this file's source/configured
    # matrix.
    return SimpleNamespace(
        provider=provider,
        workspace_id=workspace_id,
        status=status,
        base_url=None,
        extra_config=None,
        api_key_encrypted="stub-ciphertext",
    )


async def _statuses(rows: list, *, env_providers: set[str] = frozenset()) -> dict:
    svc = ModelConfigService(_db_returning(rows))
    svc._env_key_set = staticmethod(lambda p: p in env_providers)  # type: ignore[method-assign]
    out = await svc._provider_statuses("ws_1")
    return {s.provider: s for s in out}


async def test_workspace_owned_row_is_the_only_deletable_source():
    got = await _statuses([_cred("anthropic", "ws_1")])
    assert got["anthropic"].configured is True
    assert got["anthropic"].source == "workspace"


async def test_deployment_default_row_is_configured_but_not_workspace_owned():
    """A NULL-workspace row is the shared deployment default. Deleting this
    workspace's (nonexistent) row would not touch it."""
    got = await _statuses([_cred("anthropic", None)])
    assert got["anthropic"].configured is True
    assert got["anthropic"].source == "default"


async def test_env_backed_provider_reports_env_source():
    """No row at all — the resolver falls back to the process env key, which no
    API call can remove."""
    got = await _statuses([], env_providers={"anthropic"})
    assert got["anthropic"].configured is True
    assert got["anthropic"].status == "valid"
    assert got["anthropic"].source == "env"


async def test_unconfigured_provider_reports_none():
    got = await _statuses([])
    assert got["anthropic"].configured is False
    assert got["anthropic"].source == "none"


async def test_workspace_row_wins_over_the_deployment_default():
    """Both rows present: the workspace row is preferred, and it IS deletable."""
    got = await _statuses([_cred("anthropic", None), _cred("anthropic", "ws_1")])
    assert got["anthropic"].source == "workspace"
