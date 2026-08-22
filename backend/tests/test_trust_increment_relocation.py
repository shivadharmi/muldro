"""Spec §4.5 + Step 6C Task 5: trust graduates only from VERIFIED writes.

Two behaviors are characterized here:

1. build_verification_meta persists the deferred-recheck inputs on a
   completed_unverified write (the metadata the deferred tick needs).

2. Step 6C relocates the POSITIVE user-approval increment off the HTTP approve-click
   and onto the CONFIRMED-verified outcome (mirroring the auto-exec model):
     - the approve CLICK persists the user's decision_type but does NOT increment trust;
     - the REJECT click still increments (a rejection is complete — nothing to verify);
     - the increment fires on the CONFIRMED verified outcome — inline in dag_runner's
       approved-resume path, or deferred to the scheduler tick for completed_unverified;
     - decision_type ("approved"/"modified") flows through to the increment.

The DB-behavior tests skip (not fail) when Postgres is unreachable, mirroring
tests/test_deep_gate_end_to_end.py; each builds its own engine on the test's own loop.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.approvals import Approval
from src.models.task_graph import TaskRun, TaskStep
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.services.risk_assessor import RiskAssessment
from src.services.verification.readback import VerifyVerdict
from tests.conftest import make_mock_settings

# ── (1) build_verification_meta unit tests (always run) ──────────────────────────


def _verification_meta(capability, risk, verdict, output):
    from src.services.dag_runner import build_verification_meta

    return build_verification_meta(capability, risk, verdict, output)


def test_verification_meta_captures_deferred_recheck_inputs():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta("calendar.create", risk, VerifyVerdict.UNVERIFIED, {"event_id": "e1"})
    assert meta["capability"] == "calendar.create"
    assert meta["risk_level"] == "high"
    assert meta["verdict"] == "unverified"
    assert meta["reversible"] is False
    assert meta["blast_radius"] == "external_single"
    assert meta["artifact_ref"]["event_id"] == "e1"


def test_confirmed_write_needs_no_deferred_recheck():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta("calendar.create", risk, VerifyVerdict.CONFIRMED, {})
    assert meta["verdict"] == "confirmed"


# ── (2) mock-driven wiring: execute_step records on CONFIRMED, defers otherwise ──


def _make_executor(settings, db):
    from src.services.graph_executor import GraphExecutor

    return GraphExecutor(settings, db)


def _wire_common(executor, capability="email.draft"):
    """Stub the collaborators execute_step touches, but keep finalize_step + the real
    verifier so the terminal status is actually driven by the verdict (not mocked)."""
    executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
    executor._surface_emitter.emit_event = AsyncMock()
    executor._store.checkpoint = AsyncMock()
    executor._store.resolve_step_references = AsyncMock(return_value={"capability": capability})
    executor._runner.run_readback = AsyncMock(return_value=[])


def _make_step(status, capability):
    step = MagicMock()
    step.step_id = "step_wr_001"
    step.name = f"Step: {capability}"
    step.status = status  # a REAL string so transition_step validates against STEP_TRANSITIONS
    step.input_data = {"capability": capability}
    step.started_at = None
    step.completed_at = None
    step.output_data = None
    step.depends_on = []
    step.task_id = "task_001"
    step.retry_count = 0
    step.max_retries = 3
    step.timeout_seconds = None
    step.error = None
    return step


def _make_run():
    run = MagicMock()
    run.run_id = "run_001"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.status = "running"
    run.checkpoint = {}
    return run


@patch("src.services.trust_gate.get_or_assess_risk")
async def test_execute_step_records_user_approval_outcome_on_confirmed(mock_risk):
    """Approved-resume path: a CONFIRMED verified write fires the user-approval
    increment HERE (Step 6C), carrying the persisted decision_type — mirroring the
    auto-exec increment. A reversible-internal capability verifies trivially CONFIRMED."""
    settings = make_mock_settings()
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_risk.return_value = RiskAssessment(
        risk_level="low", reasoning="reversible internal", reversible=True, blast_radius="self"
    )

    executor = _make_executor(settings, mock_db)
    _wire_common(executor, capability="email.draft")
    executor._trust_gate.record_user_approval_outcome = AsyncMock()
    executor._dag_runner._read_approval_decision_type = AsyncMock(return_value="modified")

    step = _make_step(status="running", capability="email.draft")  # already_approved
    run = _make_run()

    await executor._execute_step(run, step)

    assert step.status == "completed"
    executor._trust_gate.record_user_approval_outcome.assert_awaited_once()
    args = executor._trust_gate.record_user_approval_outcome.await_args.args
    assert args[0] == "email.draft"  # capability
    assert args[1] == "low"  # risk_level (from the assessment)
    assert args[2] == "ws_test"  # workspace_id
    assert args[3] == "modified"  # the persisted decision_type flows through


async def test_deferred_tick_honors_stamped_decision_type():
    """When dag_runner stamps decision_type='modified' into the verification meta of a
    completed_unverified write, the deferred tick's CONFIRMED increment must record
    'modified' (not the hardcoded 'approved' that auto-exec writes get)."""
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = SimpleNamespace(
        step_id="stp_1",
        run_id="run_1",
        status="completed_unverified",
        completed_at=datetime.now(timezone.utc),
        input_data={"capability": "email.send"},
        output_data={
            "verification": {
                "capability": "email.send",
                "risk_level": "high",
                "reversible": False,
                "blast_radius": "external_single",
                "verdict": "unverified",
                "decision_type": "modified",
                "attempts": 1,
                "artifact_ref": {},
            }
        },
    )
    db = MagicMock()
    db.flush = AsyncMock()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    trust_write = "src.services.scheduler.deferred_verification_tick.record_approval_decision"
    with patch(trust_write, new=AsyncMock()) as record_decision:
        await _apply_recheck(db, run, step, VerifyVerdict.CONFIRMED, notifier=notifier)

    assert step.status == "completed"
    record_decision.assert_awaited_once()
    assert record_decision.await_args.args[-1] == "modified"  # user's decision_type honored


# ── (3) real-DB behavior tests (skip when Postgres unreachable) ──────────────────


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip (own throwaway loop)."""
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
                    email=f"trust-reloc-{suffix}@example.com",
                    display_name="trust-reloc-test",
                )
            )
            db.add(
                Workspace(workspace_id=workspace_id, name="trust-reloc-ws", owner_user_id=user_id)
            )
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(TaskStep).where(TaskStep.workspace_id == workspace_id))
                await db.execute(delete(TaskRun).where(TaskRun.workspace_id == workspace_id))
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
    """make_mock_settings tuned for the approval handlers: no Qdrant embedding; the
    redis publish is best-effort (swallowed) so a live Redis is not required."""
    return make_mock_settings(qdrant_url="", redis_url=get_settings().redis_url)


@_db_skip
async def test_approve_click_does_not_increment_trust_but_records_decision_type():
    """The approve CLICK must NOT increment trust (Step 6C relocates it to the verified
    outcome) but MUST persist the user's decision_type onto the approval."""
    from src.api.routes_approvals import approve_action

    async with _env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        async with factory() as db:
            db.add(
                Approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    execution_id="",
                    approval_type="step:email.send",
                    title="Send email",
                    risk_level="high",
                    status="pending",
                )
            )
            # Seed the TrustState the OLD click WOULD have incremented; its count staying
            # put is the tightest proof. The type must name a REAL capability for that
            # proof to hold: with `tool:send_email` the relocation could be reverted
            # wholesale and this still passed, because a tool name resolves to no
            # capability and so increments nothing either way.
            db.add(
                TrustState(
                    workspace_id=workspace_id,
                    capability="email.send",
                    risk_level="high",
                    approved_count=5,
                    rejected_count=0,
                    modified_count=0,
                    trust_level="learning",
                )
            )
            await db.commit()

        async with factory() as db:
            with patch("src.api.routes_approvals.AuditService") as audit_cls:
                audit_cls.return_value.log = AsyncMock()
                result = await approve_action(
                    approval_id=approval_id,
                    req=None,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=db,
                    settings=_handler_settings(),
                )
            assert result.status == "approved"

        async with factory() as db:
            ts = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == workspace_id,
                        TrustState.capability == "email.send",
                        TrustState.risk_level == "high",
                    )
                )
            ).scalar_one()
            assert ts.approved_count == 5, "the approve click must NOT increment trust"
            appr = await db.get(Approval, approval_id)
            assert appr.status == "approved"
            assert (appr.artifact_refs or {}).get("decision_type") == "approved"


@_db_skip
async def test_reject_click_still_increments_at_click():
    """A rejection is complete — nothing to verify — so the REJECT click still records
    the (negative) trust outcome at click time. This behavior is UNCHANGED by Step 6C."""
    from src.api.routes_approvals import reject_action

    async with _env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        async with factory() as db:
            db.add(
                Approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    execution_id="",
                    # `step:email.send`, not `tool:send_email`: a rejection is evidence
                    # about a CAPABILITY, and `send_email` is a tool name no catalogue
                    # contains. Recording against it created a phantom TrustState row and
                    # applied a cooldown to authority that does not exist.
                    approval_type="step:email.send",
                    title="Send email",
                    risk_level="high",
                    status="pending",
                )
            )
            db.add(
                TrustState(
                    workspace_id=workspace_id,
                    capability="email.send",
                    risk_level="high",
                    approved_count=0,
                    rejected_count=0,
                    modified_count=0,
                    trust_level="first_use",
                )
            )
            await db.commit()

        async with factory() as db:
            with patch("src.api.routes_approvals.AuditService") as audit_cls:
                audit_cls.return_value.log = AsyncMock()
                result = await reject_action(
                    approval_id=approval_id,
                    req=None,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=db,
                    settings=_handler_settings(),
                )
            assert result.status == "rejected"

        async with factory() as db:
            ts = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == workspace_id,
                        TrustState.capability == "email.send",
                        TrustState.risk_level == "high",
                    )
                )
            ).scalar_one()
            assert ts.rejected_count == 1, "the reject click MUST still record the outcome"


@_db_skip
async def test_read_approval_decision_type_reads_persisted_and_defaults():
    """The seam: _read_approval_decision_type returns the decision_type persisted on the
    step's decided Approval, and defaults to 'approved' when no approval is found."""
    async with _env() as (factory, user_id, workspace_id):
        run_id = f"run_{ULID()}"
        step_id = f"step_{ULID()}"
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    status="running",
                    source="plan",
                )
            )
            db.add(
                TaskStep(
                    step_id=step_id,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="task_1",
                    status="running",
                )
            )
            await db.flush()  # persist run+step before the Approval FKs reference them
            db.add(
                Approval(
                    approval_id=f"apr_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    execution_id="",
                    approval_type="email.send",
                    title="Send email",
                    risk_level="high",
                    status="approved",
                    decided_at=datetime.now(timezone.utc),
                    run_id=run_id,
                    step_id=step_id,
                    artifact_refs={"decision_type": "modified"},
                )
            )
            await db.commit()

        async with factory() as db:
            executor = _make_executor(make_mock_settings(), db)
            dt = await executor._dag_runner._read_approval_decision_type(
                SimpleNamespace(run_id=run_id, workspace_id=workspace_id),
                SimpleNamespace(step_id=step_id),
            )
            assert dt == "modified"

            dt_default = await executor._dag_runner._read_approval_decision_type(
                SimpleNamespace(run_id="run_absent", workspace_id=workspace_id),
                SimpleNamespace(step_id="step_absent"),
            )
            assert dt_default == "approved"


@_db_skip
async def test_record_user_approval_outcome_increments_and_preserves_decision_type():
    """The mechanism: record_user_approval_outcome increments approved_count on the
    verified outcome, and a 'modified' decision also bumps modified_count."""
    async with _env() as (factory, user_id, workspace_id):
        async with factory() as db:
            executor = _make_executor(make_mock_settings(), db)
            await executor._trust_gate.record_user_approval_outcome(
                "email.send", "high", workspace_id, "modified"
            )
            await db.commit()

        async with factory() as db:
            ts = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == workspace_id,
                        TrustState.capability == "email.send",
                        TrustState.risk_level == "high",
                    )
                )
            ).scalar_one()
            assert ts.approved_count == 1
            assert ts.modified_count == 1

        async with factory() as db:
            executor = _make_executor(make_mock_settings(), db)
            await executor._trust_gate.record_user_approval_outcome(
                "calendar.create", "high", workspace_id, "approved"
            )
            await db.commit()

        async with factory() as db:
            ts2 = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == workspace_id,
                        TrustState.capability == "calendar.create",
                        TrustState.risk_level == "high",
                    )
                )
            ).scalar_one()
            assert ts2.approved_count == 1
            assert ts2.modified_count == 0


@_db_skip
@patch("src.services.trust_gate.get_or_assess_risk")
async def test_approved_resume_confirmed_increments_trust_end_to_end(mock_risk):
    """End-to-end over a REAL DB: driving execute_step for an approved-resume step whose
    read-back verifies CONFIRMED increments trust once, honoring the persisted
    decision_type ('modified' → approved_count++ AND modified_count++)."""
    mock_risk.return_value = RiskAssessment(
        risk_level="low", reasoning="reversible internal", reversible=True, blast_radius="self"
    )
    async with _env() as (factory, user_id, workspace_id):
        run_id = f"run_{ULID()}"
        step_id = f"step_{ULID()}"
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    status="running",
                    source="plan",
                )
            )
            db.add(
                TaskStep(
                    step_id=step_id,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="task_1",
                    status="running",
                    input_data={"capability": "email.draft"},
                )
            )
            await db.flush()  # persist run+step before the Approval FKs reference them
            db.add(
                Approval(
                    approval_id=f"apr_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    execution_id="",
                    approval_type="email.draft",
                    title="Draft email",
                    risk_level="low",
                    status="approved",
                    decided_at=datetime.now(timezone.utc),
                    run_id=run_id,
                    step_id=step_id,
                    artifact_refs={"decision_type": "modified"},
                )
            )
            await db.commit()

        async with factory() as db:
            executor = _make_executor(make_mock_settings(), db)
            _wire_common(executor, capability="email.draft")
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            step = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()
            await executor._execute_step(run, step)
            await db.commit()

        async with factory() as db:
            step2 = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()
            assert step2.status == "completed"
            ts = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == workspace_id,
                        TrustState.capability == "email.draft",
                        TrustState.risk_level == "low",
                    )
                )
            ).scalar_one()
            assert ts.approved_count == 1, "CONFIRMED verified outcome must increment once"
            assert ts.modified_count == 1, "the persisted decision_type must be honored"
