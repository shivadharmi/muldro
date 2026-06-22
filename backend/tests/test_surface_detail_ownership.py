"""Tenant-ownership guard for ephemeral surface detail (cross-tenant read fix).

Ephemeral surface_ids (e.g. ``run_<run_id>``) embed a workspace-scoped record
id. Unlike the persisted path (filtered by ``user_id``), nothing verified the
caller owned that record, so a guessed id could read another tenant's detail.
``_verify_ephemeral_ownership`` closes that hole: it 404s when the referenced
record exists but belongs to a different user, and is a no-op when the record is
absent (so the builder's own empty-state still renders).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes_surface_detail import _verify_ephemeral_ownership


def _db_returning(row) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.execute.return_value = result
    return db


async def test_raises_404_when_run_owned_by_another_user():
    other = MagicMock()
    other.user_id = "usr_attacker_target"
    db = _db_returning(other)
    with pytest.raises(HTTPException) as exc:
        await _verify_ephemeral_ownership(db, {"run_id": "run_x"}, "usr_caller")
    assert exc.value.status_code == 404


async def test_passes_when_run_owned_by_caller():
    mine = MagicMock()
    mine.user_id = "usr_caller"
    db = _db_returning(mine)
    # Should not raise.
    await _verify_ephemeral_ownership(db, {"run_id": "run_x"}, "usr_caller")


async def test_noop_when_record_absent():
    # Missing record falls through to the builder's empty-state — no 404.
    db = _db_returning(None)
    await _verify_ephemeral_ownership(db, {"run_id": "run_missing"}, "usr_caller")


async def test_noop_when_no_id_reference():
    # Index-based / surface-id refs carry nothing to scope on; never queries.
    db = AsyncMock()
    await _verify_ephemeral_ownership(db, {"index": "0"}, "usr_caller")
    db.execute.assert_not_called()


async def test_guards_approval_id_reference():
    other = MagicMock()
    other.user_id = "usr_other"
    db = _db_returning(other)
    with pytest.raises(HTTPException) as exc:
        await _verify_ephemeral_ownership(db, {"approval_id": "apr_x"}, "usr_caller")
    assert exc.value.status_code == 404
