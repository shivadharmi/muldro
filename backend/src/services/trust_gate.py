"""TrustGate — the per-step risk/approval helpers for the single approval gate.

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). This owns
the side-effecting pieces of the single TrustEngine approval gate:

- ``assess_step_risk`` — call ``get_or_assess_risk`` (fail-closed to ``high`` so an
  assessment outage can never silently auto-execute a write).
- ``create_approval_and_pause`` — persist the approval, pause step+run, notify, and
  push the ``approval_needed`` surface.
- ``notify_auto_executed`` / ``record_auto_execution_outcome`` — post-execution
  notification + trust reinforcement for the auto-execute path.

The gate *decision* itself (``TrustEngine.evaluate`` + the fail-closed contract
guard) stays in the executor's step pipeline; this collaborator holds the helpers
it calls. It depends downward on ``StepGraphStore`` (checkpoint) and
``SurfaceEmitter`` (events + surface updates); it never imports ``graph_executor``.
Status changes go through ``transition_step``/``transition_run`` (never direct
mutation).
"""

from __future__ import annotations

import logging

from src.contracts import PolicyDecision
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_run, transition_step
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.risk_assessor import RiskAssessment, get_or_assess_risk
from src.services.step_graph_store import StepGraphStore

logger = logging.getLogger(__name__)


class TrustGate:
    """Risk assessment, approval persistence, and auto-execute trust feedback."""

    def __init__(
        self,
        *,
        db,
        client,
        redis=None,
        notifier_provider,
        store: StepGraphStore,
        emitter: SurfaceEmitter,
    ):
        self._db = db
        self._client = client
        self._redis = redis
        # Resolved live via a provider so the coordinator stays the single source
        # of truth (tests reassign executor._notifier after construction).
        self._notifier_provider = notifier_provider
        self._store = store
        self._emitter = emitter

    @property
    def _notifier(self):
        """Resolve the current notifier live via the provider."""
        return self._notifier_provider()

    async def assess_step_risk(
        self, capability: str, step: TaskStep, run: TaskRun
    ) -> RiskAssessment:
        """Call get_or_assess_risk with appropriate context."""
        try:
            return await get_or_assess_risk(
                capability=capability,
                step_input=step.input_data or {},
                user_context={"user_id": run.user_id},
                workspace_id=run.workspace_id or "",
                redis=self._redis,
            )
        except Exception:
            logger.warning(
                "Risk assessment failed for %s, failing closed to high (forces approval)",
                capability,
                exc_info=True,
            )
            # Fail closed: unknown risk → high → approval_required at every trust level.
            return RiskAssessment(
                risk_level="high",
                reasoning="Fallback — risk assessment unavailable, failing closed to high",
                reversible=False,
            )

    async def create_approval_and_pause(
        self,
        run: TaskRun,
        step: TaskStep,
        capability: str,
        risk: RiskAssessment,
        decision: PolicyDecision,
        surface_id: str | None = None,
    ) -> None:
        """Create approval record, pause step and run, notify user."""
        from src.services.approval_service import create_approval

        # Preview of *what* will be executed so the approval card has context
        # (CLAUDE.md: an approval needs run_id + artifact_refs).
        artifact_refs = {
            "capability": capability,
            "step_name": step.name or capability,
            "description": (step.input_data or {}).get("description") or "",
            "reversible": risk.reversible,
            "blast_radius": risk.blast_radius,
        }

        approval = await create_approval(
            self._db,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            approval_type=f"step:{capability}",
            title=f"Approve step: {step.name or capability}",
            summary=decision.justification or f"Trust gate: {risk.reasoning}",
            risk_level=risk.risk_level,
            execution_id=run.run_id,
            run_id=run.run_id,
            step_id=step.step_id,
            requested_by=run.user_id,
            artifact_refs=artifact_refs,
        )
        transition_step(step, "running")
        transition_step(step, "waiting_approval")
        # Idempotent: a sibling step in the same ready batch may have already
        # paused the run. awaiting_approval → awaiting_approval is not a legal
        # transition, so only move the run when it isn't already paused there.
        if run.status != "awaiting_approval":
            transition_run(run, "awaiting_approval")
        await self._store.checkpoint(run, step.step_id, "approval_gate")
        await self._db.flush()

        await self._emitter.emit_event(
            "approval_requested",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "approval_id": approval.approval_id,
                "capability": capability,
                "risk_level": risk.risk_level,
                "trust_decision": decision.decision,
            },
            workspace_id=run.workspace_id,
        )

        if self._notifier:
            try:
                await self._notifier.notify(
                    user_id=run.user_id,
                    notification_type="approval_request",
                    title=f"Approve: {step.name or capability}",
                    body=decision.justification or risk.reasoning,
                    data={
                        "approval_id": approval.approval_id,
                        "run_id": run.run_id,
                        "step_id": step.step_id,
                        "risk_level": risk.risk_level,
                    },
                    workspace_id=run.workspace_id,
                )
            except Exception:
                logger.warning("Failed to notify for step approval", exc_info=True)

        # Surface update: approval needed
        if surface_id:
            from src.contracts import ApprovalContext

            await self._emitter.emit_surface_update(
                surface_id=surface_id,
                user_id=run.user_id,
                phase="approval_needed",
                approval=ApprovalContext(
                    approval_id=approval.approval_id,
                    step_description=step.name or capability,
                    risk_level=risk.risk_level,
                    trust_level=decision.trust_level,
                    expires_at=(approval.expires_at.isoformat() if approval.expires_at else None),
                    triggering_step_id=step.step_id,
                    graduation_hint=decision.justification or "",
                    risk_reasoning=risk.reasoning,
                    trust_context=decision.justification or "",
                    reversible=risk.reversible,
                    blast_radius=risk.blast_radius,
                    effective_trust_level=decision.effective_trust_level,
                    approved_count=decision.approved_count,
                    rejected_count=decision.rejected_count,
                ),
                workspace_id=run.workspace_id,
            )

    async def notify_auto_executed(
        self,
        run: TaskRun,
        step: TaskStep,
        risk: RiskAssessment,
        output: dict | None,
    ) -> None:
        """Send post-execution notification for auto_execute_notify."""
        if not self._notifier:
            return

        capability = (step.input_data or {}).get(
            "capability", (step.input_data or {}).get("task_type", "unknown")
        )
        try:
            await self._notifier.notify(
                user_id=run.user_id,
                notification_type="auto_execute_notify",
                title=f"Auto-executed: {step.name or capability}",
                body=risk.reasoning,
                data={
                    "run_id": run.run_id,
                    "step_id": step.step_id,
                    "capability": capability,
                    "risk_level": risk.risk_level,
                },
                workspace_id=run.workspace_id,
            )
        except Exception:
            logger.warning("Failed to send auto_execute notification", exc_info=True)

    async def record_auto_execution_outcome(
        self, capability: str, risk_level: str, workspace_id: str
    ) -> None:
        """Reinforce trust after a successful auto-executed step.

        Treats a successful autonomous execution as a positive outcome
        (``approved``), so trust graduates from the loop's own successes — not
        only from explicit user approvals. Best-effort: a metrics/trust write
        must never fail an otherwise-successful step.
        """
        if not capability:
            return
        try:
            from src.services.risk_assessor import record_approval_decision

            async with self._db.begin_nested():
                await record_approval_decision(
                    self._db, workspace_id, capability, risk_level, "approved"
                )
        except Exception:
            logger.debug("Failed to record auto-execution trust outcome", exc_info=True)

    async def record_user_approval_outcome(
        self, capability: str, risk_level: str, workspace_id: str, decision_type: str
    ) -> None:
        """Record a user-approved write's trust outcome AFTER it verified CONFIRMED.

        The positive increment for a human-approved write fires here (on the verified
        outcome), NOT at approval-click — mirroring record_auto_execution_outcome. Preserves
        the user's decision_type ("approved"/"modified"). Best-effort in a SAVEPOINT so a
        failed trust write never poisons the run's own commit."""
        if not capability:
            return
        try:
            from src.services.risk_assessor import record_approval_decision

            async with self._db.begin_nested():
                await record_approval_decision(
                    self._db, workspace_id, capability, risk_level, decision_type
                )
        except Exception:
            logger.debug("Failed to record user-approval trust outcome", exc_info=True)
