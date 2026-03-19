"""ContextBuilder — assembles rich context packs for agent prompts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.services.artifact_store import ArtifactStore
    from src.services.goal_tracker import GoalTracker
    from src.services.memory_service import MemoryService
    from src.services.procedure_library import ProcedureLibrary
    from src.services.tool_registry import ToolRegistry
    from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)


def _rank_entities(entities: list[dict]) -> list[dict]:
    """Cross-source ranking for entities.

    Composite score: 0.40*importance + 0.30*recency + 0.30*interaction_frequency.
    """

    def _score(e: dict) -> float:
        importance = e.get("importance_score", 0.0) or 0.0
        interactions = e.get("interaction_count", 0) or 0
        # Normalize interaction count (cap at 50 for scoring)
        interaction_norm = min(interactions / 50.0, 1.0)
        # Recency: entities with last_seen_at get a boost (binary for simplicity)
        recency = 0.8 if e.get("last_seen_at") else 0.2
        return 0.40 * importance + 0.30 * recency + 0.30 * interaction_norm

    return sorted(entities, key=_score, reverse=True)


def _rank_memories(memories: list[dict]) -> list[dict]:
    """Cross-source ranking for memories.

    Composite score: 0.40*relevance + 0.25*confidence + 0.20*stability + 0.15*recency.
    """

    def _score(m: dict) -> float:
        relevance = m.get("relevance_score", 0.5) or 0.5
        confidence = m.get("confidence", 0.5) or 0.5
        stability = m.get("stability_score", 0.5) or 0.5
        # Recency: memories with higher refresh_count are more active
        refresh = m.get("refresh_count", 0) or 0
        recency = min(refresh / 10.0, 1.0)
        return 0.40 * relevance + 0.25 * confidence + 0.20 * stability + 0.15 * recency

    return sorted(memories, key=_score, reverse=True)


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
        tool_registry: ToolRegistry | None = None,
        db: AsyncSession | None = None,
    ):
        self._world_model = world_model
        self._memory_service = memory_service
        self._goal_tracker = goal_tracker
        self._procedure_library = procedure_library
        self._artifact_store = artifact_store
        self._tool_registry = tool_registry
        self._db = db

    async def build(
        self,
        user_id: str,
        query: str,
        task_type: str | None = None,
        workspace_id: str = "",
    ) -> ContextPack:
        """Build a context pack for the given query/task."""
        pack = ContextPack(task_summary=query)

        # Entities
        entity_ids: list[str] = []
        if self._world_model and query:
            try:
                entities = await self._world_model.find_entity(
                    user_id,
                    query,
                    workspace_id=workspace_id,
                )
                # Cross-source ranking: score entities by composite signal
                entities = _rank_entities(entities)
                pack.entities = entities[:10]
                entity_ids = [e["entity_id"] for e in pack.entities]
            except Exception:
                logger.debug("Entity lookup failed", exc_info=True)

        # Memory — pass entity_ids for entity-overlap ranking boost
        if self._memory_service and query:
            try:
                memories = await self._memory_service.retrieve(
                    user_id,
                    query,
                    entity_refs=entity_ids or None,
                    max_results=10,
                    workspace_id=workspace_id,
                )
                # Cross-source ranking: score memories by composite signal
                memories = _rank_memories(memories)
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

        # Related runs — recent TaskRuns for context
        if self._db:
            try:
                pack.related_runs = await self._fetch_related_runs(
                    user_id,
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.debug("Related runs fetch failed", exc_info=True)

        # Tool options — available tools for this task type
        if self._tool_registry and task_type:
            try:
                tools = await self._tool_registry.list_for_task_type(task_type)
                pack.tool_options = [t.name for t in tools]
            except Exception:
                logger.debug("Tool options fetch failed", exc_info=True)

        # Constraints — from active goals with deadlines
        if pack.goals:
            for g in pack.goals:
                if g.get("priority") == "critical":
                    pack.constraints.append(f"Critical goal: {g['title']}")

        # Risks — entity importance + deadline proximity
        if pack.entities:
            for e in pack.entities:
                importance = e.get("importance_score", 0)
                if importance and importance > 0.8:
                    name = e.get("canonical_name") or e.get("name", "unknown")
                    pack.risks.append(f"High-importance entity involved: {name}")

        return pack

    async def _fetch_related_runs(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Fetch recent completed/failed TaskRuns for context."""
        from sqlalchemy import select

        from src.models.task_graph import TaskRun

        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.user_id == user_id,
                TaskRun.workspace_id == workspace_id,
                TaskRun.status.in_(["completed", "failed"]),
            )
            .order_by(TaskRun.created_at.desc())
            .limit(5)
        )
        runs = result.scalars().all()
        return [
            {
                "run_id": r.run_id,
                "status": r.status,
                "source": r.source,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ]

    @staticmethod
    def to_prompt(pack: ContextPack, max_tokens: int = 3000) -> str:
        """Convert a context pack into a prompt string for system context injection.

        Applies priority-based truncation if the total exceeds max_tokens.
        Priority order: goals > entities > events > preferences > artifacts > procedures.
        Rough estimate: 1 token ≈ 4 characters.
        """
        max_chars = max_tokens * 4
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
            ent_lines = []
            for e in pack.entities:
                name = e.get("canonical_name") or e.get("name", "unknown")
                etype = e.get("entity_type", "?")
                parts = [f"- {name} ({etype})"]
                importance = e.get("importance_score")
                if importance and importance > 0.7:
                    parts.append(f"importance={importance:.1f}")
                last_seen = e.get("last_seen_at")
                if last_seen:
                    parts.append(f"last_seen={last_seen[:10]}")
                interactions = e.get("interaction_count")
                if interactions and interactions > 1:
                    parts.append(f"interactions={interactions}")
                ent_lines.append(" ".join(parts))
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

        if pack.related_runs:
            run_lines = [
                f"- {r.get('run_id', '?')} ({r.get('status', '?')}, {r.get('source', '?')})"
                for r in pack.related_runs
            ]
            sections.append("## Recent Runs\n" + "\n".join(run_lines))

        if pack.tool_options:
            sections.append("## Available Tools\n" + ", ".join(pack.tool_options))

        if pack.constraints:
            sections.append("## Constraints\n" + "\n".join(f"- {c}" for c in pack.constraints))

        if pack.risks:
            sections.append("## Risks\n" + "\n".join(f"- {r}" for r in pack.risks))

        result = "\n\n".join(sections) if sections else ""
        if len(result) > max_chars:
            result = result[:max_chars] + "\n\n[context truncated]"
        return result
