"""Evidence Bundle Service — builds reusable context bundles.

Assembles entity refs, memory refs, source refs, route info, confidence,
and risk level into an EvidenceBundle for side panels and inline display.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.evidence_models import (
    EntityRef,
    EvidenceBundle,
    MemoryRef,
    SourceRef,
)

logger = logging.getLogger(__name__)


class EvidenceBundleService:
    """Builds evidence bundles from traces, memories, entities, and sources."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def build_for_message(
        self,
        conversation_id: str,
        message_id: str,
    ) -> EvidenceBundle:
        """Build an evidence bundle for a specific message."""
        entities = await self._get_relevant_entities(conversation_id)
        memories = await self._get_relevant_memories(conversation_id)
        sources = await self._get_trace_sources(conversation_id)

        return EvidenceBundle(
            entities=entities,
            memories=memories,
            sources=sources,
        )

    async def build_for_run(self, run_id: str) -> EvidenceBundle:
        """Build an evidence bundle for a task run."""
        from src.models.task_graph import TaskRun, TaskStep

        sources: list[SourceRef] = []

        # Get run details
        result = await self._db.execute(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workspace_id == self._workspace_id,
            )
        )
        run = result.scalar_one_or_none()
        if not run:
            return EvidenceBundle()

        # Add trace as source
        if run.trace_id:
            sources.append(
                SourceRef(
                    source_type="trace",
                    source_id=run.trace_id,
                    label=f"Trace {run.trace_id[:16]}...",
                )
            )

        # Get step artifacts as sources
        step_result = await self._db.execute(
            select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
        )
        for step in step_result.scalars().all():
            if step.output_data and step.output_data.get("artifact_id"):
                sources.append(
                    SourceRef(
                        source_type="artifact",
                        source_id=step.output_data["artifact_id"],
                        label=f"Step output: {step.step_type}",
                    )
                )

        return EvidenceBundle(
            sources=sources,
            route_info=run.policy_decision,
        )

    async def build_for_briefing(self, briefing_id: str) -> EvidenceBundle:
        """Build an evidence bundle for a briefing."""
        from src.models.briefings import Briefing

        result = await self._db.execute(
            select(Briefing).where(
                Briefing.briefing_id == briefing_id,
                Briefing.workspace_id == self._workspace_id,
            )
        )
        briefing = result.scalar_one_or_none()
        if not briefing:
            return EvidenceBundle()

        entities = await self._get_relevant_entities_for_text(
            briefing.full_text or briefing.headline or ""
        )

        return EvidenceBundle(
            entities=entities,
            sources=[],
            confidence=None,
        )

    async def _get_relevant_entities(self, conversation_id: str) -> list[EntityRef]:
        """Get entities related to a conversation."""
        from src.models.entities import Entity

        result = await self._db.execute(
            select(Entity)
            .where(Entity.workspace_id == self._workspace_id)
            .order_by(Entity.updated_at.desc())
            .limit(10)
        )
        return [
            EntityRef(
                entity_id=e.entity_id,
                name=e.canonical_name or "",
                entity_type=e.entity_type or "",
                relevance=0.5,
            )
            for e in result.scalars().all()
        ]

    async def _get_relevant_entities_for_text(self, text: str) -> list[EntityRef]:
        """Get entities relevant to a text snippet (name matching)."""
        from src.models.entities import Entity

        if not text:
            return []

        result = await self._db.execute(
            select(Entity)
            .where(Entity.workspace_id == self._workspace_id)
            .order_by(Entity.updated_at.desc())
            .limit(20)
        )
        refs = []
        text_lower = text.lower()
        for e in result.scalars().all():
            name = e.canonical_name or ""
            if name.lower() in text_lower:
                refs.append(
                    EntityRef(
                        entity_id=e.entity_id,
                        name=name,
                        entity_type=e.entity_type or "",
                        relevance=0.8,
                    )
                )
        return refs[:10]

    async def _get_relevant_memories(self, conversation_id: str) -> list[MemoryRef]:
        """Get memories related to a conversation."""
        from src.models.memory import Memory

        result = await self._db.execute(
            select(Memory)
            .where(Memory.workspace_id == self._workspace_id)
            .order_by(Memory.updated_at.desc())
            .limit(5)
        )
        return [
            MemoryRef(
                memory_id=m.memory_id,
                content=(m.fact_text or "")[:200],
                memory_type=m.memory_type or "episodic",
                relevance=0.5,
            )
            for m in result.scalars().all()
        ]

    async def _get_trace_sources(self, conversation_id: str) -> list[SourceRef]:
        """Get trace sources for a conversation."""
        from src.models.conversations import Conversation
        from src.models.traces import Trace

        # Get conversation's trace references
        result = await self._db.execute(
            select(Conversation).where(
                Conversation.conversation_id == conversation_id,
                Conversation.workspace_id == self._workspace_id,
            )
        )
        conv = result.scalar_one_or_none()
        if not conv:
            return []

        # Get recent traces for this workspace
        trace_result = await self._db.execute(
            select(Trace)
            .where(Trace.workspace_id == self._workspace_id)
            .order_by(Trace.started_at.desc())
            .limit(3)
        )
        return [
            SourceRef(
                source_type="trace",
                source_id=t.trace_id,
                label=f"Trace: {t.trigger or 'system'}",
            )
            for t in trace_result.scalars().all()
        ]
