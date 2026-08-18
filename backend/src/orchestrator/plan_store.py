"""PlanStore — persists plans and lightweight interaction logs to the DB.

Extracted from ``MuldroOrchestrator`` (god-object decomposition, 2026-06-19).
A leaf collaborator: it depends only on the DB session factory. Converts a
``PlanOutput`` into DB ``Plan`` + ``PlanTask`` rows (so the Executor/GraphExecutor
can execute it) and records ``InteractionLog`` audit rows for simple interactions.
"""

import logging

from ulid import ULID

from src.contracts import PlanOutput

logger = logging.getLogger(__name__)


def _build_step_to_task_map(steps: list) -> dict[str, str]:
    """First pass: create step_id -> task_id mapping for ALL steps.

    Pre-builds the full mapping so that forward dependencies (e.g. step s1
    depends on s3, which appears later in the list) resolve correctly.
    Includes both muldro and user actor steps since user steps can be
    dependency targets.
    """
    step_to_task: dict[str, str] = {}
    for step in steps:
        if step.step_id:
            step_to_task[step.step_id] = f"ptask_{ULID()}"
    return step_to_task


class PlanStore:
    """Durable persistence for plans and interaction logs."""

    def __init__(self, db_factory_provider):
        # Provider (not a captured value) so reassigning db_factory on the
        # orchestrator propagates to this collaborator (see EventPublisher).
        self._db_factory_provider = db_factory_provider

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    async def persist_plan_record(
        self,
        plan_output: PlanOutput,
        user_id: str,
        workspace_id: str,
        trigger_type: str = "user_message",
        idempotency_key: str | None = None,
    ) -> PlanOutput:
        """Persist a Plan + PlanTasks to DB, returning PlanOutput with plan_id set.

        Converts PlanOutput steps into DB Plan + PlanTask rows so the Governor
        can evaluate_policy(plan_id) and the Executor can execute via
        GraphExecutor — both require a DB-backed Plan.

        Both ``muldro`` and ``user`` actor steps become PlanTasks. User-actor
        steps are persisted with ``task_type="user_action"`` and
        ``status="awaiting_input"`` so they appear as dependency targets and
        in execution surfaces.

        A two-pass approach pre-builds step_id→task_id mappings so forward
        dependencies (e.g. s1 depends on s3) resolve correctly.

        Args:
            trigger_type: Origin — "user_message" (interactive) or "perception"
                          (autonomous observation).
            idempotency_key: Optional dedup key to prevent duplicate perception plans.
        """
        from src.models.plans import Plan, PlanTask

        plan_id = f"plan_{ULID()}"

        # Risk ordinals for deriving max risk
        risk_ord: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
        ord_risk: dict[int, str] = {v: k for k, v in risk_ord.items()}

        try:
            async with self._db_factory() as db:
                # Idempotency check — skip if an active plan with this key exists
                if idempotency_key:
                    from sqlalchemy import select

                    existing = await db.execute(
                        select(Plan.plan_id).where(
                            Plan.idempotency_key == idempotency_key,
                            Plan.workspace_id == workspace_id,
                            Plan.status.notin_(["completed", "failed", "cancelled"]),
                        )
                    )
                    existing_plan_id = existing.scalar_one_or_none()
                    if existing_plan_id:
                        logger.info(
                            "Skipping duplicate plan: idempotency_key=%s",
                            idempotency_key,
                        )
                        return plan_output.model_copy(update={"plan_id": existing_plan_id})

                # Pass 1: Pre-build step_id → task_id map for ALL steps
                # so forward dependencies resolve correctly.
                step_to_task = _build_step_to_task_map(plan_output.steps)

                # Pass 2: Create PlanTask records for every step.
                tasks: list[PlanTask] = []
                max_risk_ord = 0

                for step in plan_output.steps:
                    max_risk_ord = max(max_risk_ord, risk_ord.get(step.risk, 0))

                    # Reuse the pre-assigned task_id (or generate one for
                    # steps without a step_id).
                    task_id = step_to_task.get(step.step_id, f"ptask_{ULID()}")

                    # Map step depends_on step_ids to task_ids
                    dep_task_ids = [
                        step_to_task[dep] for dep in step.depends_on if dep in step_to_task
                    ]

                    if step.actor == "user":
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type="user_action",
                                input_data={
                                    "description": step.description,
                                    "capability": step.capability,
                                },
                                depends_on=dep_task_ids or None,
                                status="awaiting_input",
                            )
                        )
                    else:
                        step_input = dict(step.input) if step.input else {}
                        if step.description:
                            step_input["description"] = step.description
                        if step.capability:
                            step_input["capability"] = step.capability
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type=step.capability,
                                input_data=step_input,
                                depends_on=dep_task_ids or None,
                                status="pending",
                            )
                        )

                # Derive risk_level and execution_mode from max step risk
                risk_level = ord_risk.get(max_risk_ord, "low")
                execution_mode = "approval_required" if max_risk_ord >= 2 else "auto_execute"

                plan_record = Plan(
                    plan_id=plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trigger_type=trigger_type,
                    trigger_ref=None,
                    idempotency_key=idempotency_key,
                    goal=plan_output.goal or "",
                    priority=plan_output.priority,
                    decision="plan",
                    reasoning_summary=plan_output.reasoning or None,
                    risk_level=risk_level,
                    execution_mode=execution_mode,
                    status="created",
                    success_conditions=(
                        {"criteria": plan_output.success_criteria}
                        if plan_output.success_criteria
                        else None
                    ),
                    plan_output_json=plan_output.model_dump(mode="json"),
                )
                plan_record.tasks = tasks
                db.add(plan_record)
                await db.commit()

            logger.info(
                "Persisted plan %s tasks=%d risk=%s",
                plan_id,
                len(tasks),
                risk_level,
            )
            return plan_output.model_copy(update={"plan_id": plan_id})
        except Exception:
            logger.warning("Failed to persist plan record", exc_info=True)
            return plan_output

    async def log_interaction(
        self,
        user_id: str,
        workspace_id: str,
        trace_id: str,
        message_preview: str | None = None,
        intent: str | None = None,
        plan: "PlanOutput | None" = None,
        conversation_id: str | None = None,
        response_preview: str | None = None,
        run_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> str | None:
        """Create a lightweight InteractionLog record for auditing.

        Replaces _create_lightweight_run + _complete_lightweight_run.
        Returns the interaction_id on success, None on failure.
        """
        from src.models.interaction_log import InteractionLog

        interaction_id = f"ilog_{ULID()}"
        try:
            async with self._db_factory() as db:
                db.add(
                    InteractionLog(
                        interaction_id=interaction_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        message_preview=(message_preview[:500] if message_preview else None),
                        plan_summary=(plan.reasoning[:500] if plan and plan.reasoning else None),
                        plan_id=plan.plan_id if plan else None,
                        run_id=run_id,
                        intent=intent,
                        response_preview=(response_preview[:500] if response_preview else None),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                        latency_ms=latency_ms,
                    )
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to log interaction", exc_info=True)
            return None
        return interaction_id
