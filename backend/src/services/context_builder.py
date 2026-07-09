"""ContextBuilder — assembles rich context packs for agent prompts."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.services.artifact_store import ArtifactStore
    from src.services.graph_engine import GraphEngine
    from src.services.memory_service import MemoryService
    from src.services.reranker_service import RerankerService
    from src.services.tool_registry import ToolRegistry
    from src.services.tri_search import TriSearchService
    from src.services.vector_store import VectorStore
    from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)


# Continuous recency decay: exp(-lambda * days_since(last_seen_at)). A 30-day
# half-life mirrors the recency windows used in tri_search / memory retrieval,
# keeping recency weighting consistent across the codebase.
_RECENCY_HALFLIFE_DAYS = 30.0
_RECENCY_LAMBDA = math.log(2) / _RECENCY_HALFLIFE_DAYS


def _recency_score(
    last_seen_at: "str | datetime | None", *, now: "datetime | None" = None
) -> float:
    """Continuous recency in [0, 1]: exp(-lambda * days_since(last_seen_at)).

    1.0 at last_seen == now, ~0.5 at 30 days, decaying smoothly. Replaces the old
    binary 0.8/0.2. A missing or unparseable timestamp -> 0.0 (no recency signal;
    importance + interaction still contribute to the composite).
    """
    if not last_seen_at:
        return 0.0
    if now is None:
        now = datetime.now(timezone.utc)
    ts = last_seen_at
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return math.exp(-_RECENCY_LAMBDA * days)


def _rank_entities(entities: list[dict]) -> list[dict]:
    """Cross-source ranking for entities.

    Composite score: 0.40*importance + 0.30*recency + 0.30*interaction_frequency.
    """

    def _score(e: dict) -> float:
        importance = e.get("importance_score", 0.0) or 0.0
        interactions = e.get("interaction_count", 0) or 0
        # Normalize interaction count (cap at 50 for scoring)
        interaction_norm = min(interactions / 50.0, 1.0)
        # Continuous recency decay (replaces the old binary 0.8/0.2).
        recency = _recency_score(e.get("last_seen_at"))
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

    model_config = ConfigDict(extra="ignore")

    task_summary: str | None = None
    goals: list[dict] = []
    entities: list[dict] = []
    graph_relationships: list[dict] = []  # B5: Neo4j entity relationships
    recent_events: list[dict] = []
    related_runs: list[dict] = []
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
        artifact_store: ArtifactStore | None = None,
        tool_registry: ToolRegistry | None = None,
        db: AsyncSession | None = None,
        graph_engine: GraphEngine | None = None,
        vector_store: VectorStore | None = None,
        tri_search: TriSearchService | None = None,
        reranker: RerankerService | None = None,
    ):
        self._world_model = world_model
        self._memory_service = memory_service
        self._artifact_store = artifact_store
        self._tool_registry = tool_registry
        self._db = db
        self._graph_engine = graph_engine
        self._vector_store = vector_store
        self._tri_search = tri_search
        self._reranker = reranker

    async def build(
        self,
        user_id: str,
        query: str,
        task_type: str | None = None,
        workspace_id: str = "",
        jit: bool = False,
    ) -> ContextPack:
        """Build a context pack for the given query/task.

        ``jit=True`` (Step 8) builds a SLIM always-on core only — explicit
        preferences, active goals, and a compact top-N entity list — via cheap
        direct queries, skipping the bulky semantic/graph/memory retrieval below.
        Bulky detail is retrieved on demand by the agent via existing tools.
        ``jit=False`` (default) is the full eager pack, unchanged.
        """
        pack = ContextPack(task_summary=query)

        if jit:
            # SLIM: always-on core only — cheap, largely query-independent. Bulky
            # detail (full entities, graph, memories) is retrieved on demand via tools.
            pack.preferences = await self._fetch_core_preferences(user_id, workspace_id)
            pack.goals = await self._fetch_core_goals(user_id, workspace_id)
            pack.entities = await self._fetch_core_entities(user_id, workspace_id)
            return pack

        # Try TriSearch for unified context retrieval
        if self._tri_search and self._db:
            try:
                grouped = await self._tri_search.search_for_context(
                    query=query,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=self._db,
                    limit=20,
                )
                # Populate pack from grouped TriSearch results
                pack.entities = [
                    {
                        "entity_id": r.get("id", ""),
                        "entity_type": r.get("result_type", "entity"),
                        "canonical_name": r.get("title", ""),
                        **r,
                    }
                    for r in grouped.get("entity", [])
                ][:10]
                episodic = [
                    r for r in grouped.get("memory", []) if r.get("memory_type") == "episodic"
                ]
                pack.recent_events = episodic
                pack.preferences = [
                    r for r in grouped.get("memory", []) if r.get("memory_type") == "preference"
                ]
                # Still fetch goals, artifacts separately below
            except Exception:
                logger.debug(
                    "TriSearch context assembly failed, using individual services",
                    exc_info=True,
                )

        # Entities
        entity_ids: list[str] = []
        if not pack.entities and self._world_model and query:
            try:
                entities = await self._world_model.resolve_entities(
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

        # B5: Neo4j graph relationships for discovered entities
        if self._graph_engine and entity_ids:
            try:
                for eid in entity_ids[:5]:
                    related = await self._graph_engine.traverse_weighted(
                        entity_id=eid,
                        user_id=user_id,
                        depth=2,
                        min_strength=0.3,
                    )
                    for r in related[:8]:
                        pack.graph_relationships.append(
                            {
                                "entity_id": r["entity_id"],
                                "name": r["name"],
                                "entity_type": r.get("entity_type"),
                                "strength": r.get("avg_strength", 0.5),
                                "distance": r.get("distance", 1),
                                "attributes": r.get("attributes"),
                            }
                        )
            except Exception:
                logger.debug("Graph relationship lookup failed", exc_info=True)

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

        # D3: Explicit preference fetch — ensures active preferences are
        # included even when they don't semantically match the current query.
        # Bounded to max_results=20 to prevent unbounded context growth.
        if self._memory_service:
            try:
                all_prefs = await self._memory_service.get_user_preferences(
                    user_id, workspace_id=workspace_id, max_results=20
                )
                existing_ids = {p.get("memory_id") for p in pack.preferences}
                for p in all_prefs:
                    pid = p.get("memory_id") or p.get("id", "")
                    if pid and pid not in existing_ids:
                        pack.preferences.append(
                            {"memory_id": pid, "fact_text": p.get("fact_text", ""), **p}
                        )
            except Exception:
                logger.debug("Explicit preference fetch failed", exc_info=True)

        # Hard cap preferences after merge (semantic + explicit)
        pack.preferences = pack.preferences[:25]

        # Goals — retrieved from memory system (memory_type="goal")
        if self._memory_service:
            try:
                goal_memories = await self._memory_service.retrieve(
                    user_id,
                    query=query,
                    memory_types=["goal"],
                    max_results=5,
                    workspace_id=workspace_id,
                )
                pack.goals = [
                    {
                        "memory_id": g.get("memory_id", ""),
                        "title": g.get("fact_text", ""),
                        "confidence": g.get("confidence", 0.5),
                        "priority": (g.get("provenance") or {}).get("priority", "medium"),
                    }
                    for g in goal_memories
                ]
            except Exception:
                logger.debug("Goal memory fetch failed", exc_info=True)

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

    async def _fetch_core_preferences(self, user_id: str, workspace_id: str) -> list[dict]:
        """All active preferences (D3), non-semantic — mirrors the eager explicit-pref fetch."""
        if not self._memory_service:
            return []
        try:
            prefs = await self._memory_service.get_user_preferences(
                user_id, workspace_id=workspace_id, max_results=20
            )
            return [
                {
                    "memory_id": p.get("memory_id") or p.get("id", ""),
                    "fact_text": p.get("fact_text", ""),
                    **p,
                }
                for p in prefs
            ][:25]
        except Exception:
            logger.debug("Core preference fetch failed", exc_info=True)
            return []

    async def _fetch_core_goals(self, user_id: str, workspace_id: str) -> list[dict]:
        """Active goal memories via a DIRECT query (no embed/Qdrant/stability writes)."""
        if not self._db:
            return []
        try:
            from sqlalchemy import select

            from src.models.memory import Memory

            result = await self._db.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.workspace_id == workspace_id,
                    Memory.memory_type == "goal",
                    Memory.status == "active",
                )
                .order_by(Memory.created_at.desc())
                .limit(5)
            )
            return [
                {
                    "memory_id": g.memory_id,
                    "title": g.fact_text,
                    "confidence": g.confidence,
                    # slim core flattens priority (no provenance in the direct query);
                    # eager path derives it from provenance.priority
                    "priority": "medium",
                }
                for g in result.scalars().all()
            ]
        except Exception:
            logger.debug("Core goal fetch failed", exc_info=True)
            return []

    async def _fetch_core_entities(
        self, user_id: str, workspace_id: str, limit: int = 8
    ) -> list[dict]:
        """Top-N entities by importance/recency via a DIRECT query (NOT semantic resolution)."""
        if not self._db:
            return []
        try:
            from sqlalchemy import select

            from src.models.entities import Entity  # NOTE: plural module name

            result = await self._db.execute(
                select(Entity)
                .where(Entity.user_id == user_id, Entity.workspace_id == workspace_id)
                .order_by(Entity.importance_score.desc(), Entity.last_seen_at.desc().nullslast())
                .limit(limit)
            )
            return [
                {
                    "entity_id": e.entity_id,
                    "canonical_name": e.canonical_name,
                    "entity_type": e.entity_type,
                }
                for e in result.scalars().all()
            ]
        except Exception:
            logger.debug("Core entity fetch failed", exc_info=True)
            return []

    @staticmethod
    def to_prompt(pack: ContextPack, jit: bool = False) -> str:
        """Convert a context pack into a prompt string for system context injection.

        ``jit=True`` (Step 8) renders a terse entity block (no eager decoration)
        and appends a trailing retrieval-hint section pointing the agent at the
        tools it can use to pull bulky detail on demand. ``jit=False`` (default)
        is byte-identical to the pre-Step-8 rendering.
        """
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
            if jit:
                ent_lines = []
                for e in pack.entities:
                    name = e.get("canonical_name") or e.get("name", "unknown")
                    etype = e.get("entity_type", "?")
                    ent_lines.append(f"- {name} ({etype})")
                sections.append("## Known Entities\n" + "\n".join(ent_lines))
            else:
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
                    confidence = e.get("confidence")
                    if confidence is not None:
                        parts.append(f"confidence={confidence:.2f}")
                        if confidence < 0.5:
                            # Abstention hint for the agent (ask/verify before relying on
                            # this). NOT a gate signal — confidence never gates (spec §4.3).
                            parts.append("[low confidence — verify before relying]")
                    ent_lines.append(" ".join(parts))
                sections.append("## Relevant Entities\n" + "\n".join(ent_lines))

        if pack.graph_relationships:
            rel_lines = []
            for r in pack.graph_relationships[:10]:
                name = r.get("name") or r.get("canonical_name", "?")
                etype = r.get("entity_type", "?")
                strength = r.get("strength")
                distance = r.get("distance")
                rtype = r.get("relation_type", "")
                parts = [f"- {name} ({etype})"]
                if rtype:
                    parts.append(f"via {rtype}")
                if strength is not None:
                    parts.append(f"strength={strength:.1f}")
                if distance is not None:
                    parts.append(f"distance={distance}")
                rel_lines.append(" ".join(parts))
            sections.append("## Entity Relationships\n" + "\n".join(rel_lines))

        if pack.preferences:
            pref_lines = [f"- {p.get('fact_text', '')}" for p in pack.preferences]
            sections.append("## User Preferences\n" + "\n".join(pref_lines))

        if pack.recent_events:
            evt_lines = [f"- {e.get('fact_text', '')}" for e in pack.recent_events]
            sections.append("## Recent Context\n" + "\n".join(evt_lines))

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

        if jit:
            sections.append(
                "## Retrieving More Context\n"
                "Only a compact core is preloaded. Use `get_entity`, `query_facts`, "
                "`traverse`, `get_provenance`, and `search` to retrieve entity detail, "
                "facts, relationships, and memories on demand."
            )

        return "\n\n".join(sections) if sections else ""


def _rerank_by_relevance(items: list[dict]) -> list[dict]:
    """Rerank items by combined static + semantic relevance score.

    Used in the fallback path (non-TriSearch) to ensure the most
    relevant items survive any downstream caps or truncation.
    """
    for item in items:
        static = item.get("_static_rank", 0.5)
        semantic = item.get("relevance_score", item.get("score", 0.5))
        item["_combined_score"] = 0.6 * semantic + 0.4 * static
    return sorted(items, key=lambda x: x.get("_combined_score", 0), reverse=True)
