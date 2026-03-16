"""ContextBuilder — assembles rich context packs for agent prompts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.services.artifact_store import ArtifactStore
    from src.services.goal_tracker import GoalTracker
    from src.services.memory_service import MemoryService
    from src.services.procedure_library import ProcedureLibrary
    from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)


class ContextPack(BaseModel):
    """Structured context assembled for an agent prompt."""

    task_summary: str | None = None
    goals: list[dict] = []
    entities: list[dict] = []
    recent_events: list[dict] = []
    related_runs: list[dict] = []
    procedures: list[dict] = []
    preferences: list[dict] = []
    artifacts: list[dict] = []
    constraints: list[str] = []
    tool_options: list[str] = []
    risks: list[str] = []


class ContextBuilder:
    """Build rich context packs from the world model, memory, and services."""

    def __init__(
        self,
        world_model: WorldModel | None = None,
        memory_service: MemoryService | None = None,
        goal_tracker: GoalTracker | None = None,
        procedure_library: ProcedureLibrary | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self._world_model = world_model
        self._memory_service = memory_service
        self._goal_tracker = goal_tracker
        self._procedure_library = procedure_library
        self._artifact_store = artifact_store

    async def build(
        self,
        user_id: str,
        query: str,
        task_type: str | None = None,
    ) -> ContextPack:
        """Build a context pack for the given query/task."""
        pack = ContextPack(task_summary=query)

        # Entities
        if self._world_model and query:
            try:
                entities = await self._world_model.find_entity(user_id, query)
                pack.entities = entities[:10]
            except Exception:
                logger.debug("Entity lookup failed", exc_info=True)

        # Memory
        if self._memory_service and query:
            try:
                memories = await self._memory_service.retrieve(user_id, query, max_results=10)
                pack.recent_events = [m for m in memories if m.get("memory_type") == "episodic"]
                pack.preferences = [m for m in memories if m.get("memory_type") == "preference"]
            except Exception:
                logger.debug("Memory retrieval failed", exc_info=True)

        # Goals
        if self._goal_tracker:
            try:
                goals = await self._goal_tracker.list_goals(user_id, status="active")
                pack.goals = [
                    {
                        "goal_id": g.goal_id,
                        "title": g.title,
                        "progress": g.progress,
                        "priority": getattr(g, "priority", "medium"),
                    }
                    for g in goals[:5]
                ]
            except Exception:
                logger.debug("Goal fetch failed", exc_info=True)

        # Procedures
        if self._procedure_library and task_type:
            try:
                procs = await self._procedure_library.find_procedures(user_id, task_type=task_type)
                pack.procedures = [{"name": p.name, "steps": p.steps_json} for p in procs[:3]]
            except Exception:
                logger.debug("Procedure lookup failed", exc_info=True)

        # Artifacts
        if self._artifact_store and query:
            try:
                artifacts = await self._artifact_store.search(user_id, query, limit=5)
                pack.artifacts = [
                    {
                        "artifact_id": a.artifact_id,
                        "artifact_type": a.artifact_type,
                        "title": a.title,
                    }
                    for a in artifacts
                ]
            except Exception:
                logger.debug("Artifact search failed", exc_info=True)

        return pack

    @staticmethod
    def to_prompt(pack: ContextPack) -> str:
        """Convert a context pack into a prompt string for system context injection."""
        sections = []

        if pack.task_summary:
            sections.append(f"## Task\n{pack.task_summary}")

        if pack.goals:
            goal_lines = [
                f"- {g['title']} (progress: {g.get('progress', 0):.0%}, "
                f"priority: {g.get('priority', 'medium')})"
                for g in pack.goals
            ]
            sections.append("## Active Goals\n" + "\n".join(goal_lines))

        if pack.entities:
            ent_lines = [
                f"- {e.get('canonical_name', 'unknown')} ({e.get('entity_type', '?')})"
                for e in pack.entities
            ]
            sections.append("## Relevant Entities\n" + "\n".join(ent_lines))

        if pack.preferences:
            pref_lines = [f"- {p.get('fact_text', '')}" for p in pack.preferences]
            sections.append("## User Preferences\n" + "\n".join(pref_lines))

        if pack.recent_events:
            evt_lines = [f"- {e.get('fact_text', '')}" for e in pack.recent_events]
            sections.append("## Recent Context\n" + "\n".join(evt_lines))

        if pack.procedures:
            proc_lines = [f"- Procedure: {p.get('name', 'unnamed')}" for p in pack.procedures]
            sections.append("## Known Procedures\n" + "\n".join(proc_lines))

        if pack.artifacts:
            art_lines = [
                f"- [{a.get('artifact_type', '?')}] {a.get('title', 'untitled')}"
                for a in pack.artifacts
            ]
            sections.append("## Artifacts\n" + "\n".join(art_lines))

        if pack.constraints:
            sections.append("## Constraints\n" + "\n".join(f"- {c}" for c in pack.constraints))

        if pack.risks:
            sections.append("## Risks\n" + "\n".join(f"- {r}" for r in pack.risks))

        return "\n\n".join(sections) if sections else ""
