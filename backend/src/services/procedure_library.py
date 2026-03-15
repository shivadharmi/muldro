"""Procedure library — learns and stores reusable workflow patterns.

Analyzes completed executions to extract repeatable patterns,
then matches incoming events against known procedures.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.executions import Execution
from src.models.plans import Plan, PlanTask
from src.models.procedures import Procedure

logger = logging.getLogger(__name__)


class ProcedureLibrary:
    """Learns and stores reusable workflow patterns from execution history."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def extract_procedure(self, execution_id: str, user_id: str) -> Procedure | None:
        """Analyze a completed execution and extract a reusable pattern."""
        result = await self._db.execute(
            select(Execution).where(
                Execution.execution_id == execution_id,
                Execution.status == "completed",
            )
        )
        execution = result.scalar_one_or_none()
        if not execution:
            return None

        result = await self._db.execute(select(Plan).where(Plan.plan_id == execution.plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            return None

        result = await self._db.execute(
            select(PlanTask).where(PlanTask.plan_id == plan.plan_id).order_by(PlanTask.id)
        )
        tasks = list(result.scalars().all())
        if not tasks:
            return None

        # Build task template from completed tasks
        task_template = []
        for task in tasks:
            task_template.append(
                {
                    "task_type": task.task_type,
                    "input_template": self._generalize_input(task.input_data),
                    "depends_on_index": [],  # Simplified for now
                }
            )

        # Derive trigger pattern from the plan's source event
        trigger_pattern = {
            "event_type": plan.decision,
            "source": plan.source_event_id,
        }

        procedure = Procedure(
            procedure_id=f"proc_{ULID()}",
            user_id=user_id,
            name=f"Learned: {plan.goal}",
            description=f"Extracted from execution {execution_id}",
            trigger_pattern=trigger_pattern,
            task_template=task_template,
            learned_from=[execution_id],
            confidence=0.5,
            status="draft",
        )
        self._db.add(procedure)
        await self._db.flush()

        logger.info(
            "Procedure extracted: %s from execution %s (%d tasks)",
            procedure.procedure_id,
            execution_id,
            len(tasks),
        )
        return procedure

    async def find_matching(
        self, user_id: str, event_type: str, source: str | None = None
    ) -> list[Procedure]:
        """Find procedures that match an incoming event pattern."""
        result = await self._db.execute(
            select(Procedure).where(
                Procedure.user_id == user_id,
                Procedure.status == "active",
            )
        )
        procedures = result.scalars().all()

        matched = []
        for proc in procedures:
            pattern = proc.trigger_pattern or {}
            if pattern.get("event_type") and pattern["event_type"] != event_type:
                continue
            if source and pattern.get("source") and pattern["source"] != source:
                continue
            matched.append(proc)

        return sorted(matched, key=lambda p: p.confidence, reverse=True)

    async def get_procedures(self, user_id: str, status: str | None = None) -> list[Procedure]:
        """Get all procedures for a user."""
        query = select(Procedure).where(Procedure.user_id == user_id)
        if status:
            query = query.where(Procedure.status == status)
        query = query.order_by(Procedure.created_at.desc())

        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def activate_procedure(self, procedure_id: str, user_id: str) -> bool:
        """Activate a draft procedure."""
        result = await self._db.execute(
            select(Procedure).where(
                Procedure.procedure_id == procedure_id,
                Procedure.user_id == user_id,
            )
        )
        proc = result.scalar_one_or_none()
        if not proc:
            return False

        proc.status = "active"
        await self._db.flush()
        return True

    async def record_usage(self, procedure_id: str, success: bool) -> None:
        """Record that a procedure was used and update confidence."""
        result = await self._db.execute(
            select(Procedure).where(Procedure.procedure_id == procedure_id)
        )
        proc = result.scalar_one_or_none()
        if not proc:
            return

        proc.usage_count += 1
        proc.last_used_at = datetime.now(timezone.utc)

        # Adjust confidence based on success/failure
        if success:
            proc.confidence = min(1.0, proc.confidence + 0.05)
        else:
            proc.confidence = max(0.0, proc.confidence - 0.1)

        await self._db.flush()

    @staticmethod
    def _generalize_input(input_data: dict | None) -> dict:
        """Generalize specific input data into a reusable template."""
        if not input_data:
            return {}
        # Keep structure but replace specific values with placeholders
        template = {}
        for key, value in input_data.items():
            if isinstance(value, str) and len(value) > 50:
                template[key] = "{{" + key + "}}"
            else:
                template[key] = value
        return template
