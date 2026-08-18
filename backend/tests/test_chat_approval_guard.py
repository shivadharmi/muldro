"""Step 10D P2.4 [Sec-I2]: the autonomous decision endpoints REFUSE chat approvals.

A chat single-lead approval (the action-time ``permission_gate`` persists it with
``artifact_refs["chat"] is True``) is resumed via ``POST /v1/muldro/chat/resume`` — NEVER
via ``/v1/approvals/{id}/approve|reject``. If a chat approval reached those handlers it
would (a) consume the ``pending`` status, so the paired ``/chat/resume`` continuation then
refuses ``status != pending`` and strands an empty chat bubble, and (b) on reject, feed the
autonomous ``TrustState`` a decision from a path that is not trust-graduated. The guard
(``_guard_not_chat_approval``) rejects at the TOP of BOTH handlers — before any status
mutation or trust feedback — with a clean 409.

Two layers: unit (the guard helper in isolation) + real-DB (the handlers 409 and leave the
row untouched). The DB test skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.api.routes_approvals import _guard_not_chat_approval, approve_action, reject_action
from src.config.settings import get_settings
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from tests.conftest import make_mock_settings

# ── unit: the guard helper in isolation ──────────────────────────────────────────


def test_guard_raises_409_for_chat_approval():
    approval = SimpleNamespace(artifact_refs={"chat": True, "thread_id": "c:ws:t1"})
    with pytest.raises(HTTPException) as exc:
        _guard_not_chat_approval(approval)
    assert exc.value.status_code == 409
    assert "/v1/muldro/chat/resume" in exc.value.detail


def test_guard_allows_non_chat_approval():
    # An autonomous approval (no chat marker) passes through untouched.
    _guard_not_chat_approval(SimpleNamespace(artifact_refs={"tool_name": "send_email"}))
    _guard_not_chat_approval(SimpleNamespace(artifact_refs={"chat": False}))
    _guard_not_chat_approval(SimpleNamespace(artifact_refs=None))
    _guard_not_chat_approval(SimpleNamespace(artifact_refs={}))
    # STRICT is-True: a truthy-but-not-True value never fires (the gate stores literal True).
    _guard_not_chat_approval(SimpleNamespace(artifact_refs={"chat": "yes"}))
    # MagicMock-truthy hazard: an autonomous-approval test double whose artifact_refs is a
    # bare MagicMock (not a dict) must NOT be mistaken for a chat approval — the isinstance
    # dict guard fail-safes toward the untouched autonomous path.
    _guard_not_chat_approval(SimpleNamespace(artifact_refs=MagicMock()))


# ── real-DB: the handlers 409 and leave the row PENDING ──────────────────────────


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
    except Exception:  # pragma: no cover - environment-dependent
        return False


_DB_UP = _db_reachable()
_db_skip = pytest.mark.skipif(not _DB_UP, reason="Postgres not reachable")


@asynccontextmanager
async def _env():
    """Yield ``(factory, user_id, workspace_id)`` with FK parents seeded; clean up after."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"chat-guard-{suffix}@example.com",
                    display_name="chat-guard-test",
                )
            )
            db.add(
                Workspace(workspace_id=workspace_id, name="chat-guard-ws", owner_user_id=user_id)
            )
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(TrustState).where(TrustState.workspace_id == workspace_id))
                await db.execute(
                    delete(TrustCeiling).where(TrustCeiling.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


def _handler_settings():
    return make_mock_settings(qdrant_url="", redis_url=get_settings().redis_url)


async def _seed_chat_approval(factory, user_id, workspace_id) -> str:
    approval_id = f"apr_{ULID()}"
    async with factory() as db:
        db.add(
            Approval(
                approval_id=approval_id,
                user_id=user_id,
                workspace_id=workspace_id,
                execution_id="",
                approval_type="tool:send_email",
                title="Send email",
                risk_level="high",
                status="pending",
                # The permission_gate marks chat approvals with chat=True + lead_scope.
                artifact_refs={
                    "chat": True,
                    "thread_id": f"c:{workspace_id}:t1",
                    "lead_scope": ["email.send"],
                    "permission_mode": "ask",
                },
            )
        )
        await db.commit()
    return approval_id


@_db_skip
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_decision_endpoint_409s_and_leaves_chat_approval_pending(action):
    handler = approve_action if action == "approve" else reject_action
    async with _env() as (factory, user_id, workspace_id):
        approval_id = await _seed_chat_approval(factory, user_id, workspace_id)

        async with factory() as db:
            # Patch AuditService/record so a leak past the guard would be observable AND
            # harmless — the guard must raise BEFORE any of them is reached.
            with patch("src.api.routes_approvals.AuditService") as audit_cls:
                audit_cls.return_value.log = AsyncMock()
                with pytest.raises(HTTPException) as exc:
                    await handler(
                        approval_id=approval_id,
                        req=None,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        db=db,
                        settings=_handler_settings(),
                    )
            assert exc.value.status_code == 409
            assert "/v1/muldro/chat/resume" in exc.value.detail
            # The guard fired before AuditService was constructed (no trust/audit side effects).
            audit_cls.assert_not_called()

        # The row is UNTOUCHED — still pending, so /chat/resume can still consume it.
        async with factory() as db:
            row = await db.get(Approval, approval_id)
            assert row.status == "pending"
            assert row.decided_at is None
