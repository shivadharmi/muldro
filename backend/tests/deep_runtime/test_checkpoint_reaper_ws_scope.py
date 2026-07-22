"""Step 10A NEW-1: workspace-scope the checkpoint-reaper decided-approval sweep.

``sweep_decided_approval_checkpoints`` previously scanned ``Approval.thread_id`` across ALL
tenants before deleting checkpoints. Now that A6 (``thread_identity.make_thread_id``) embeds
the workspace in the ``thread_id``, this proves two additions:

1. An optional ``workspace_id`` filter lets the sweep run scoped to one tenant.
2. A thread whose A6-embedded workspace disagrees with its approval's ``workspace_id`` is
   NEVER reaped (cross-tenant thread_id consistency guard) — even on a global (unscoped) sweep.

Real-DB (skips, does not fail, when Postgres is unreachable) — mirrors the harness in
``tests/test_checkpoint_reaper.py``, extended to seed TWO tenants on one engine/factory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.checkpoint_reaper import sweep_decided_approval_checkpoints
from src.deep_runtime.thread_identity import make_thread_id
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from tests.test_checkpoint_reaper import _FakeSaver, _seed_approval, requires_db


@asynccontextmanager
async def _two_ws_env():
    """Yield ``(factory, user_a, ws_a, user_b, ws_b)`` with BOTH FK chains seeded on one
    engine/factory. Teardown deletes Approvals + TrustStates + TrustCeilings for both
    workspaces, then both Workspaces + Users, then disposes the engine."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix_a = str(ULID())
    suffix_b = str(ULID())
    user_a = f"usr_{suffix_a}"
    ws_a = f"ws_{suffix_a}"
    user_b = f"usr_{suffix_b}"
    ws_b = f"ws_{suffix_b}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_a,
                    email=f"reaper-ws-scope-a-{suffix_a}@example.com",
                    display_name="checkpoint-reaper-ws-scope-test-a",
                )
            )
            db.add(Workspace(workspace_id=ws_a, name="reaper-ws-scope-a", owner_user_id=user_a))
            db.add(
                User(
                    user_id=user_b,
                    email=f"reaper-ws-scope-b-{suffix_b}@example.com",
                    display_name="checkpoint-reaper-ws-scope-test-b",
                )
            )
            db.add(Workspace(workspace_id=ws_b, name="reaper-ws-scope-b", owner_user_id=user_b))
            await db.commit()
        yield factory, user_a, ws_a, user_b, ws_b
    finally:
        try:
            async with factory() as db:
                for ws in (ws_a, ws_b):
                    await db.execute(delete(Approval).where(Approval.workspace_id == ws))
                    await db.execute(delete(TrustState).where(TrustState.workspace_id == ws))
                    await db.execute(delete(TrustCeiling).where(TrustCeiling.workspace_id == ws))
                for ws in (ws_a, ws_b):
                    await db.execute(delete(Workspace).where(Workspace.workspace_id == ws))
                for user in (user_a, user_b):
                    await db.execute(delete(User).where(User.user_id == user))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


@requires_db
async def test_ws_scoped_sweep_reaps_only_target_tenant():
    """Seed ws_A and ws_B each with one decided-old (48h) approval whose thread_id embeds
    their own workspace. A sweep scoped to ws_A must reap ONLY ws_A's thread."""
    async with _two_ws_env() as (factory, user_a, ws_a, user_b, ws_b):
        now = datetime.now(timezone.utc)
        tid_a = make_thread_id(ws_a)
        tid_b = make_thread_id(ws_b)

        await _seed_approval(
            factory,
            user_id=user_a,
            workspace_id=ws_a,
            thread_id=tid_a,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )
        await _seed_approval(
            factory,
            user_id=user_b,
            workspace_id=ws_b,
            thread_id=tid_b,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )

        fake = _FakeSaver()
        reaped = await sweep_decided_approval_checkpoints(
            fake, factory, workspace_id=ws_a, retention_hours=24
        )

        deleted = set(fake.deleted)
        assert tid_a in deleted, "the target tenant's thread must be reaped"
        assert tid_b not in deleted, "another tenant's thread must NOT be reaped"
        assert reaped == 1, f"expected exactly one reaped thread, got {reaped}"


@requires_db
async def test_cross_tenant_embedded_ws_mismatch_never_reaped():
    """A thread whose A6-embedded workspace (ws_B) disagrees with its approval's own
    workspace_id (ws_A) must NEVER be reaped — even on a global (unscoped) sweep."""
    async with _two_ws_env() as (factory, user_a, ws_a, user_b, ws_b):
        now = datetime.now(timezone.utc)
        mismatched_tid = make_thread_id(ws_b)  # embeds ws_B ...

        await _seed_approval(
            factory,
            user_id=user_a,
            workspace_id=ws_a,  # ... but the approval itself belongs to ws_A
            thread_id=mismatched_tid,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )

        fake = _FakeSaver()
        reaped = await sweep_decided_approval_checkpoints(fake, factory, retention_hours=24)

        assert mismatched_tid not in set(fake.deleted), (
            "a thread whose embedded workspace disagrees with its approval's workspace_id "
            "must never be reaped"
        )
        assert reaped == 0, f"expected zero reaped threads (consistency guard), got {reaped}"


@requires_db
async def test_pending_guard_survives_under_ws_scope():
    """The existing per-thread ``decided - pending`` guard must still hold when a
    ``workspace_id`` filter is applied: a decided-old approval sharing a thread with a
    still-pending sibling (same tenant) must NOT be reaped."""
    async with _two_ws_env() as (factory, user_a, ws_a, user_b, ws_b):
        now = datetime.now(timezone.utc)
        shared_tid = make_thread_id(ws_a)

        await _seed_approval(
            factory,
            user_id=user_a,
            workspace_id=ws_a,
            thread_id=shared_tid,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )
        await _seed_approval(
            factory,
            user_id=user_a,
            workspace_id=ws_a,
            thread_id=shared_tid,
            status="pending",
            decided_at=None,
        )

        fake = _FakeSaver()
        reaped = await sweep_decided_approval_checkpoints(
            fake, factory, workspace_id=ws_a, retention_hours=24
        )

        assert shared_tid not in set(fake.deleted), (
            "a thread with a still-PENDING approval must NOT be reaped, even under ws scope"
        )
        assert reaped == 0, f"expected zero reaped threads (pending sibling protects), got {reaped}"
