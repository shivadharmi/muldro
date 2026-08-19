"""Approval endpoints — list, approve, and reject pending actions."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import ApprovalDecisionRequest, ApprovalDetailResponse, ApprovalResponse
from src.config.settings import Settings, get_settings
from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.middleware.security import RATE_LIMIT_APPROVAL_DECISION, per_endpoint_rate_limit
from src.models.approvals import Approval
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep
from src.services.audit import AuditService
from src.services.execution_state import transition_run, transition_step
from src.services.graph_executor import create_graph_executor
from src.services.prepared_actions import execute_prepared_action

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v1/approvals/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval_detail(
    approval_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a single approval, including execution and plan context."""
    result = await db.execute(
        select(Approval).where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

    # Get plan goal and trace_id via TaskRun
    plan_goal = None
    trace_id = None
    if approval.execution_id:
        run_result = await db.execute(
            select(TaskRun).where(TaskRun.run_id == approval.execution_id)
        )
        run = run_result.scalar_one_or_none()
        if run:
            trace_id = run.trace_id
            if run.plan_id:
                plan_result = await db.execute(select(Plan.goal).where(Plan.plan_id == run.plan_id))
                plan_goal = plan_result.scalar_one_or_none()

    return ApprovalDetailResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        approval_type=approval.approval_type,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        decision_reason=approval.decision_reason,
        execution_id=approval.execution_id,
        plan_goal=plan_goal,
        artifact_refs=approval.artifact_refs,
        trace_id=trace_id,
    )


@router.get("/v1/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str = "pending",
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List approvals for the user, filtered by status."""
    result = await db.execute(
        select(Approval)
        .where(
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
            Approval.status == status,
        )
        .order_by(Approval.created_at.desc())
        .limit(50)
    )
    approvals = result.scalars().all()
    return [
        ApprovalResponse(
            approval_id=a.approval_id,
            status=a.status,
            title=a.title,
            summary=a.summary,
            risk_level=a.risk_level,
            created_at=a.created_at,
        )
        for a in approvals
    ]


def _guard_not_chat_approval(approval) -> None:
    """Reject chat-turn approvals at the TOP of the autonomous decision endpoints (Sec-I2).

    A chat single-lead approval (Step 10D P2.4) carries ``artifact_refs["chat"] is True`` and
    is resumed via ``POST /v1/muldro/chat/resume`` — NEVER these endpoints. Letting one flow
    through here would (a) consume the ``pending`` status, so the paired ``/chat/resume`` turn
    then refuses ``status != pending`` and strands an empty chat bubble, and (b) on reject,
    feed ``record_approval_decision`` a chat decision that would pollute the autonomous
    ``TrustState`` (chat is not trust-graduated). Called BEFORE any status mutation / trust
    feedback so a mis-routed chat approval is a clean 409, no side effects. The WS decision
    bridge delegates to these same handlers, so this covers that path too.

    STRICT ``is True`` on a real ``dict`` (never a bare truthiness): the permission_gate
    persists ``chat`` as the literal ``True``, and a strict check refuses to fire on a
    non-dict ``artifact_refs`` (e.g. a bare ``MagicMock`` in autonomous-approval tests) —
    fail-safe toward the AUTONOMOUS path, which is the untouched default.
    """
    refs = approval.artifact_refs
    if isinstance(refs, dict) and refs.get("chat") is True:
        raise HTTPException(
            status_code=409,
            detail=(
                "This approval belongs to a chat turn; resume it via POST /v1/muldro/chat/resume."
            ),
        )


async def _run_prepared_action(approval, *, user_id: str, settings):
    """Execute a confirmed PREPARED action and record the outcome on the Approval.

    The status moves to ``executed`` or ``failed`` rather than staying ``approved``, so the
    prepared-work queue drops it either way and the founder can see which it was. This never
    raises for a policy refusal — ``execute_prepared_action`` fails closed and returns the
    reason, which is persisted so the queue can show it.

    ``redis`` and ``ledger`` are BOTH load-bearing, not optional. ``execute_prepared_action``
    defaults them to ``None`` for unit tests, and those defaults are fail-OPEN: without the
    ledger a double-confirm double-fires an external write (invariant 5 does not hold at all),
    and without redis a prepared confirm does not mutually exclude with a concurrent chat write
    to the same capability. Supplying both is this function's main job.

    ``user_id`` is the CONFIRMER, used for audit. The action itself replays as the PREPARER
    (``execute_prepared_action`` reads ``approval.user_id``) — a replay runs with the authority
    of the turn that produced it, not the authority of whoever clicked approve.
    """
    from src.api.routes_chat import _get_orchestrator
    from src.models.database import get_session_factory
    from src.services.idempotency import IdempotencyLedger

    db_factory = get_session_factory()
    orchestrator = await _get_orchestrator(settings)
    redis = None
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.warning(
            "prepared action %s running WITHOUT the cross-path write lock — redis unavailable",
            approval.approval_id,
            exc_info=True,
        )

    try:
        outcome = await execute_prepared_action(
            approval,
            execute_tool=orchestrator._execute_tool,
            db_factory=db_factory,
            redis=redis,
            ledger=IdempotencyLedger(db_factory),
        )
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.debug("redis close failed", exc_info=True)

    approval.status = "executed" if outcome.executed else "failed"
    refs = {**(approval.artifact_refs or {})}
    if outcome.error:
        refs["prepared_error"] = outcome.error
    approval.artifact_refs = refs
    logger.info(
        "[prepared] %s confirmed by %s -> %s", approval.approval_id, user_id, approval.status
    )
    return outcome


async def _publish_prepared_decision(
    approval, *, user_id: str, workspace_id: str, event: str, settings
) -> None:
    """Announce a prepared-action decision on the workspace agent stream.

    Same event names and payload keys as the normal approve/reject publishes, with
    ``run_id: None`` — a prepared action has no run, and inventing one would be a lie. The
    prepared-work queue is a LIVE surface: without this it silently keeps showing work that
    has already run until someone refreshes by hand.

    Best-effort, like the paths it mirrors. By the time this fires the external write has
    already happened, so a dead event bus must never turn a success into an error — losing a
    notification is not losing the action.
    """
    redis = None
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(workspace_id)
        await bus.publish(
            stream,
            event,
            {"approval_id": approval.approval_id, "run_id": None},
            user_id,
            workspace_id=workspace_id,
        )
    except Exception:
        logger.debug("Failed to publish %s event", event, exc_info=True)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.debug("redis close failed", exc_info=True)


@router.post(
    "/v1/approvals/{approval_id}/approve",
    response_model=ApprovalResponse,
    dependencies=[Depends(per_endpoint_rate_limit(RATE_LIMIT_APPROVAL_DECISION))],
)
async def approve_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Approve a pending action and trigger execution."""
    approval = await _get_approval(
        db, approval_id, user_id, workspace_id, intended_action="approve"
    )
    _guard_not_chat_approval(approval)

    # Idempotent: already approved — return without re-executing (T6)
    if approval.status == "approved":
        return ApprovalResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            title=approval.title,
            summary=approval.summary,
            risk_level=approval.risk_level,
            created_at=approval.created_at,
        )

    approval.status = "approved"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None
    approval.approved_by = user_id

    # Resolve the linked run for event emission and downstream resume.
    # Do NOT transition the run here — resume_run() and execute_run()
    # handle their own state transitions after re-reading the row.
    effective_run_id = approval.run_id or approval.execution_id
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == effective_run_id))
    run = run_result.scalar_one_or_none()

    # Emit approval_resolved runtime event
    try:
        from src.models.runtime_event import RuntimeEvent

        db.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                run_id=approval.run_id,
                step_id=approval.step_id,
                event_type="approval_resolved",
                payload={
                    "approval_id": approval_id,
                    "decision": "approved",
                    "reason": req.reason if req else None,
                },
            )
        )
    except Exception:
        pass

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_approved",
        workspace_id=workspace_id,
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Approved: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    # Step 6C: the POSITIVE trust increment is relocated to the CONFIRMED-verified outcome
    # (dag_runner approved-resume / deferred tick), mirroring the auto-exec model — NOT fired
    # here at click. Persist the user's decision_type so the verified-outcome hook can use it.
    decision_type = "modified" if req and req.reason else "approved"
    approval.artifact_refs = {**(approval.artifact_refs or {}), "decision_type": decision_type}

    await db.commit()

    # PREPARED actions: the action was fully derived on the original turn and recorded on this
    # row. Confirmation REPLAYS that recorded payload — it must NOT be routed through
    # GraphExecutor, whose agent would re-derive it and could run something other than what
    # the founder reviewed.
    if (approval.artifact_refs or {}).get("prepared") is True:
        await _run_prepared_action(approval, user_id=user_id, settings=settings)
        await db.commit()
        await _publish_prepared_decision(
            approval,
            user_id=user_id,
            workspace_id=workspace_id,
            event="approval.approved",
            settings=settings,
        )
        return ApprovalResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            title=approval.title,
            summary=approval.summary,
            risk_level=approval.risk_level,
            created_at=approval.created_at,
        )

    # Embed approval decision into Qdrant
    try:
        from src.services.embedding_service import EmbeddingService
        from src.services.vector_store import VectorStore

        if settings.qdrant_url:
            vs = VectorStore(settings)
            es = EmbeddingService(settings)
            await _embed_approval_decision(
                approval_id=approval_id,
                approval_type=approval.approval_type or "",
                summary=approval.summary or "",
                risk_level=approval.risk_level or "low",
                outcome="approved",
                user_id=user_id,
                embedding_service=es,
                vector_store=vs,
                workspace_id=workspace_id,
            )
    except Exception:
        logger.debug("Approval embedding failed", exc_info=True)

    # Publish approval.approved domain event via SSE
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(workspace_id)
        await bus.publish(
            stream,
            "approval.approved",
            {"approval_id": approval_id, "run_id": approval.execution_id},
            user_id,
            workspace_id=workspace_id,
        )
        await redis.aclose()
    except Exception:
        logger.debug("Failed to publish approval.approved event", exc_info=True)

    # Queue the run for scheduler pickup instead of executing synchronously.
    # The scheduler has full agent loop dependencies (db_factory, execute_tool_fn,
    # budget) that are not available in the API route context. Tagging the run
    # with source="approval_resume" signals the scheduler to resume it via
    # resume_run() on the next tick (≤30s).
    if approval.run_id:
        try:
            step_result = await db.execute(
                select(TaskStep)
                .where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == approval.run_id,
                )
                .with_for_update()
            )
            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                transition_step(step, "running")

            # Re-fetch run after the earlier commit may have expired it
            run_result2 = await db.execute(
                select(TaskRun).where(TaskRun.run_id == approval.run_id).with_for_update()
            )
            run_for_resume = run_result2.scalar_one_or_none()
            if run_for_resume:
                run_for_resume.source = "approval_resume"
            await db.commit()
            logger.info(
                "Step-level approval queued for scheduler: run=%s step=%s",
                approval.run_id,
                approval.step_id,
            )
        except Exception as exc:
            logger.exception("Failed to queue run for resume: %s", approval.run_id)
            await _mark_run_failed_after_resume(db, approval.run_id, exc)
    elif run and run.plan_id:
        # Plan-level approval: tag for scheduler pickup
        try:
            run_result2 = await db.execute(
                select(TaskRun).where(TaskRun.run_id == run.run_id).with_for_update()
            )
            run_for_resume = run_result2.scalar_one_or_none()
            if run_for_resume:
                run_for_resume.source = "approval_resume"
            await db.commit()
            logger.info(
                "Plan-level approval queued for scheduler: run=%s",
                run.run_id,
            )
        except Exception as exc:
            logger.exception("Failed to queue plan-level run: %s", run.run_id)
            await _mark_run_failed_after_resume(db, run.run_id, exc)
    elif approval.artifact_refs and approval.artifact_refs.get("tool_name"):
        # Tool-level approval resume — create a background TaskRun.
        # The scheduler picks up source="approval_resume" runs with full deps.
        try:
            from src.models.plans import PlanTask

            tool_name = approval.artifact_refs["tool_name"]
            tool_params = approval.artifact_refs.get("tool_params", {})
            plan_id = f"plan_{ULID()}"
            plan = Plan(
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                trigger_type="approval_resume",
                goal=f"Execute approved tool: {tool_name}",
                priority="high",
                decision="plan",
                status="created",
            )
            plan.tasks = [
                PlanTask(
                    task_id=f"ptask_{ULID()}",
                    plan_id=plan_id,
                    workspace_id=workspace_id,
                    task_type=tool_name,
                    input_data=tool_params,
                    status="pending",
                )
            ]
            db.add(plan)

            bg_run = TaskRun(
                run_id=f"run_{ULID()}",
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                source="approval_resume",
                status="pending",
                trace_id=None,
            )
            db.add(bg_run)
            await db.flush()

            # Populate steps so the scheduler can execute immediately
            executor = await create_graph_executor(
                settings=settings, db=db, workspace_id=workspace_id
            )
            await executor.populate_run_steps(bg_run.run_id, plan_id)
            await db.commit()

            logger.info(
                "Tool-level approval queued for scheduler: %s → run %s",
                approval.approval_id,
                bg_run.run_id,
            )
        except Exception as exc:
            logger.exception(
                "Failed to create resume run for tool approval: %s",
                approval.approval_id,
            )
            try:
                _bg_run_id = bg_run.run_id  # type: ignore[possibly-undefined]
            except NameError:
                _bg_run_id = None
            if _bg_run_id:
                await _mark_run_failed_after_resume(db, _bg_run_id, exc)

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


@router.post(
    "/v1/approvals/{approval_id}/reject",
    response_model=ApprovalResponse,
    dependencies=[Depends(per_endpoint_rate_limit(RATE_LIMIT_APPROVAL_DECISION))],
)
async def reject_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Reject a pending action."""
    approval = await _get_approval(db, approval_id, user_id, workspace_id, intended_action="reject")
    _guard_not_chat_approval(approval)

    # Idempotent: already rejected — return without re-executing (T6)
    if approval.status == "rejected":
        return ApprovalResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            title=approval.title,
            summary=approval.summary,
            risk_level=approval.risk_level,
            created_at=approval.created_at,
        )

    approval.status = "rejected"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None
    approval.approved_by = user_id

    # Emit approval_resolved runtime event
    try:
        from src.models.runtime_event import RuntimeEvent

        db.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                run_id=approval.run_id,
                step_id=approval.step_id,
                event_type="approval_resolved",
                payload={
                    "approval_id": approval_id,
                    "decision": "rejected",
                    "reason": req.reason if req else None,
                },
            )
        )
    except Exception:
        pass

    if (approval.artifact_refs or {}).get("prepared") is True:
        # A rejected prepared action simply never runs. There is no graph to resume and no
        # TrustState to feed — prepared work is not trust-graduated. It IS audited: refusing a
        # fully-derived external write is a founder decision, and must be as visible as
        # approving one. The normal path's audit block sits below the run machinery this
        # branch returns before, so the row is emitted here rather than skipped.
        audit = AuditService(db)
        await audit.log(
            user_id=user_id,
            action_type="approval_rejected",
            workspace_id=workspace_id,
            approval_id=approval_id,
            execution_id=approval.execution_id,
            summary=f"Rejected: {approval.title}",
            details={"reason": req.reason if req else None},
        )
        await db.commit()
        await _publish_prepared_decision(
            approval,
            user_id=user_id,
            workspace_id=workspace_id,
            event="approval.rejected",
            settings=settings,
        )
        return ApprovalResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            title=approval.title,
            summary=approval.summary,
            risk_level=approval.risk_level,
            created_at=approval.created_at,
        )

    # Cancel the run and transition the step
    effective_run_id = approval.run_id or approval.execution_id
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == effective_run_id))
    run = run_result.scalar_one_or_none()
    if run and run.status in ("awaiting_approval", "running", "paused"):
        # Transition the waiting step to cancelled
        if approval.step_id:
            step_result = await db.execute(
                select(TaskStep).where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == effective_run_id,
                )
            )
            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                transition_step(step, "cancelled")

        # Cancel the full run via graph executor (handles all remaining steps)
        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        try:
            await executor.cancel_run(effective_run_id)
        except Exception:
            # Fallback: direct state transition
            transition_run(run, "cancelled")
            logger.warning("Failed to cancel run %s via executor", effective_run_id, exc_info=True)

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_rejected",
        workspace_id=workspace_id,
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Rejected: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    # Trust feedback loop — record rejection for graduated autonomy
    try:
        from src.services.risk_assessor import record_approval_decision

        capability = approval.approval_type
        if ":" in capability:
            capability = capability.split(":", 1)[1]
        await record_approval_decision(
            db, workspace_id, capability, approval.risk_level or "low", "rejected"
        )
    except Exception:
        logger.warning("Trust feedback failed for rejection %s", approval_id, exc_info=True)

    await db.commit()

    # Embed rejection decision into Qdrant
    try:
        from src.services.embedding_service import EmbeddingService
        from src.services.vector_store import VectorStore

        if settings.qdrant_url:
            vs = VectorStore(settings)
            es = EmbeddingService(settings)
            await _embed_approval_decision(
                approval_id=approval_id,
                approval_type=approval.approval_type or "",
                summary=approval.summary or "",
                risk_level=approval.risk_level or "low",
                outcome="rejected",
                user_id=user_id,
                embedding_service=es,
                vector_store=vs,
                workspace_id=workspace_id,
            )
    except Exception:
        logger.debug("Rejection embedding failed", exc_info=True)

    # Publish approval.rejected domain event via SSE
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(workspace_id)
        await bus.publish(
            stream,
            "approval.rejected",
            {"approval_id": approval_id, "run_id": approval.execution_id},
            user_id,
            workspace_id=workspace_id,
        )
        await redis.aclose()
    except Exception:
        logger.debug("Failed to publish approval.rejected event", exc_info=True)

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


class ApprovalEditRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    risk_level: str | None = None


@router.post(
    "/v1/approvals/{approval_id}/edit",
    response_model=ApprovalResponse,
    dependencies=[Depends(per_endpoint_rate_limit(RATE_LIMIT_APPROVAL_DECISION))],
)
async def edit_approval(
    approval_id: str,
    req: ApprovalEditRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Edit a pending approval's metadata before deciding."""
    result = await db.execute(
        select(Approval).where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit approval in '{approval.status}' state",
        )

    if req.title is not None:
        approval.title = req.title
    if req.summary is not None:
        approval.summary = req.summary
    if req.risk_level is not None:
        approval.risk_level = req.risk_level

    await db.commit()

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


@router.get("/v1/approvals/{approval_id}/impact")
async def get_approval_impact(
    approval_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get impact analysis for a pending approval."""
    from src.services.approval_impact import ApprovalImpactService

    svc = ApprovalImpactService(db, workspace_id)
    impact = await svc.get_impact(approval_id)
    affected = await svc.get_affected_entities(approval_id)

    return {
        "approval_id": approval_id,
        "risk_level": impact.risk_level,
        "reversibility": impact.reversibility,
        "reversibility_detail": impact.reversibility_detail,
        "policy_explanation": impact.policy_explanation,
        "downstream_effects": impact.downstream_effects,
        "affected_entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "entity_type": e.entity_type,
                "impact_type": e.impact_type,
            }
            for e in affected
        ],
    }


async def _mark_run_failed_after_resume(db: AsyncSession, run_id: str, exc: Exception) -> None:
    """Best-effort: rollback, re-fetch run, transition to failed."""
    try:
        await db.rollback()
        result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        r = result.scalar_one_or_none()
        if r and r.status not in ("completed", "failed", "cancelled"):
            transition_run(r, "failed")
            # r.error is served verbatim by the history API — safe message + code
            # only; raw str(exc) goes to logs.
            code, message, _ = classify(exc)
            r.error = {
                "resume_failed": message,
                "error_code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
            logger.error("Approval resume failed for run %s: %s", run_id, exc, exc_info=True)
            r.completed_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception:
        logger.warning("Failed to mark run as failed after resume error", exc_info=True)


async def _embed_approval_decision(
    approval_id: str,
    approval_type: str,
    summary: str,
    risk_level: str,
    outcome: str,
    user_id: str,
    embedding_service,
    vector_store,
    workspace_id: str = "",
) -> None:
    """Embed approval decision into Qdrant (best-effort)."""
    try:
        text = f"{approval_type}: {summary} → {outcome}"
        embedding = await embedding_service.embed_text(text)
        if embedding:
            await vector_store.upsert(
                collection="approvals",
                id=approval_id,
                vector=embedding,
                payload={
                    "approval_id": approval_id,
                    "approval_type": approval_type,
                    "capability": approval_type,
                    "risk_level": risk_level,
                    "decision": outcome,
                    "outcome": outcome,
                    "workspace_id": workspace_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                user_id=user_id,
            )
    except Exception:
        logger.debug("Approval embedding failed for %s", approval_id, exc_info=True)


async def _get_approval(
    db: AsyncSession,
    approval_id: str,
    user_id: str,
    workspace_id: str,
    intended_action: str = "approve",
) -> Approval:
    """Fetch an approval with row-level locking.

    Uses SELECT ... FOR UPDATE to prevent concurrent approval race conditions.

    - Raises 404 if approval is not found.
    - Raises 410 if the approval has expired (T4).
    - Returns already-decided approvals when the status matches the intended action,
      enabling idempotent double-click protection (T6).
    - Raises 400 if the approval is already decided in a conflicting state.
    """
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

    # Check expiry before allowing action (T4)
    if (
        approval.status == "pending"
        and approval.expires_at
        and approval.expires_at < datetime.now(timezone.utc)
    ):
        approval.status = "expired"
        await db.flush()
        raise HTTPException(status_code=410, detail="Approval has expired")

    if approval.status != "pending":
        # Idempotent: if already in the intended terminal state, return it (T6)
        expected = "approved" if intended_action == "approve" else "rejected"
        if approval.status == expected:
            return approval  # caller detects via status field and short-circuits
        raise HTTPException(
            status_code=400,
            detail=f"Approval already {approval.status}",
        )

    return approval
