"""Memory/search-domain MCP tools: search, entities, goals, context, storage."""

import logging

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from sqlalchemy import select

from src.integrations.mcp_errors import make_error_response
from src.tools.intelligence_server import _shared
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def search(
    user_id: str,
    query: str,
    ctx: Context,
    types: str = "",
    limit: int = 20,
    workspace_id: str = "",
) -> dict:
    """Unified search across all knowledge: memories, entities, events.

    Uses TriSearch: vector (Qdrant) + keyword (Postgres FTS) + graph (Neo4j).
    types: comma-separated filter (e.g., "memory,entity"). Empty = all.
    """
    async with _get_db() as db:
        try:
            svc = _shared._services
            if svc and hasattr(svc, "tri_search") and svc.tri_search:
                type_list = [t.strip() for t in types.split(",") if t.strip()] or None
                results = await svc.tri_search.search(
                    query=query,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=db,
                    types=type_list,
                    limit=limit,
                )
                return {"results": results, "count": len(results)}
            # Fallback to memory service if TriSearch not available
            memory_svc = svc.memory_service if svc else None
            if memory_svc:
                results = await memory_svc.retrieve(
                    user_id,
                    query,
                    max_results=limit,
                    workspace_id=workspace_id,
                )
                return {"results": results, "count": len(results)}
            return {
                "results": [],
                "count": 0,
                "error": "No search service available",
            }
        except Exception as e:
            logger.error("search failed: %s", e, exc_info=True)
            return {"results": [], "count": 0, "error": str(e)}


@intelligence.tool(
    tags={"librarian", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def update_entity(
    entity_id: str,
    ctx: Context,
    user_id: str = "",
    attributes: str = "",
    add_alias: str = "",
    workspace_id: str = "",
) -> dict:
    """Update an entity's attributes or add an alias."""
    async with _get_db() as db:
        try:
            from src.models.entities import Entity

            result = await db.execute(
                select(Entity).where(
                    Entity.entity_id == entity_id,
                    Entity.workspace_id == workspace_id,
                )
            )
            entity = result.scalar_one_or_none()
            if not entity:
                return {"status": "not_found", "entity_id": entity_id}

            if attributes:
                import json

                try:
                    new_attrs = json.loads(attributes)
                    existing = entity.attributes or {}
                    existing.update(new_attrs)
                    entity.attributes = existing
                except json.JSONDecodeError:
                    return {"status": "error", "error": "Invalid JSON for attributes"}

            if add_alias:
                from src.models.entities import EntityAlias

                dup = await db.execute(
                    select(EntityAlias).where(
                        EntityAlias.entity_id == entity_id,
                        EntityAlias.alias == add_alias,
                    )
                )
                if dup.scalar_one_or_none() is None:
                    if add_alias.startswith("@"):
                        alias_type = "handle"
                    elif "@" in add_alias:
                        alias_type = "email"
                    else:
                        alias_type = "name"
                    db.add(
                        EntityAlias(
                            entity_id=entity_id,
                            workspace_id=workspace_id,
                            alias=add_alias,
                            alias_type=alias_type,
                        )
                    )

            await db.flush()
            await db.commit()

            # Sync to Neo4j (inline, best-effort)
            try:
                if _shared._settings and _shared._settings.neo4j_url:
                    from src.services.graph_sync import GraphSyncService

                    gs = GraphSyncService(_shared._settings, db)
                    await gs.sync_entity_by_id(entity_id)
                    await gs.close()
            except Exception:
                logger.debug(
                    "Neo4j sync after update_entity failed for %s",
                    entity_id,
                    exc_info=True,
                )

            return {"status": "updated", "entity_id": entity_id}
        except Exception as e:
            logger.error("update_entity failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"planner", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_goal_memories(
    user_id: str,
    ctx: Context,
    limit: int = 10,
    workspace_id: str = "",
) -> dict:
    """Get active user goals stored as memories.

    Goals are stored as memories with memory_type='goal' and scope='planning'.
    Returns goal text, confidence, and entity links.
    """
    async with _get_db() as db:
        try:
            from sqlalchemy import select

            from src.models.memory import Memory

            result = await db.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.workspace_id == workspace_id,
                    Memory.memory_type == "goal",
                    Memory.status == "active",
                )
                .order_by(Memory.created_at.desc())
                .limit(limit)
            )
            goals = result.scalars().all()
            return {
                "goals": [
                    {
                        "memory_id": g.memory_id,
                        "text": g.fact_text,
                        "confidence": g.confidence,
                        "entity_ids": g.entity_ids or [],
                        "created_at": g.created_at.isoformat() if g.created_at else None,
                    }
                    for g in goals
                ],
                "count": len(goals),
            }
        except Exception as e:
            logger.error("get_goal_memories failed: %s", e, exc_info=True)
            return {"goals": [], "error": str(e)}


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def build_context(
    user_id: str,
    query: str,
    ctx: Context,
    task_type: str = "",
    workspace_id: str = "",
) -> dict:
    """Build a rich context pack for a query/task.

    Returns assembled context from entities, memories, goals,
    and artifacts.
    """
    async with _get_db() as db:
        try:
            from src.services.context_builder import ContextBuilder

            await ctx.report_progress(0, 4, "Initializing context builder...")
            svc = _shared.request_services(db)
            builder = ContextBuilder(
                world_model=svc.world_model,
                memory_service=svc.memory_service,
                artifact_store=svc.artifact_store,
                db=db,
            )
            await ctx.report_progress(1, 4, "Gathering entities and memories...")
            pack = await builder.build(
                user_id,
                query,
                task_type=task_type or None,
                workspace_id=workspace_id,
            )
            await ctx.report_progress(3, 4, "Formatting context prompt...")
            prompt_text = ContextBuilder.to_prompt(pack)
            await ctx.report_progress(4, 4, "Context ready")
            return {
                "context_prompt": prompt_text,
                "entity_count": len(pack.entities),
                "goal_count": len(pack.goals),
                "memory_count": (len(pack.recent_events) + len(pack.preferences)),
            }
        except Exception as e:
            logger.error("build_context failed: %s", e, exc_info=True)
            return {"context_prompt": "", "error": str(e)}


@intelligence.tool(
    tags={"librarian", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def store_memory(
    user_id: str,
    text: str,
    ctx: Context,
    memory_type: str = "fact",
    scope: str = "general",
    ttl_days: int = 0,
    entity_ids: str = "",
    source: str = "agent",
    workspace_id: str = "",
) -> dict:
    """Store a memory in the knowledge base."""
    async with _get_db() as db:
        try:
            from src.services.memory_service import MemoryService

            # Create a MemoryService bound to THIS session so the
            # commit below actually persists the memory.
            memory_svc = MemoryService(
                settings=_shared._settings,
                db=db,
                vector_store=_shared._services.vector_store,
            )

            linked_ids = (
                [e.strip() for e in entity_ids.split(",") if e.strip()] if entity_ids else None
            )

            if memory_type == "goal":
                mid = await memory_svc.store_goal_memory(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    title=text,
                    entity_ids=linked_ids,
                )
            elif memory_type == "briefing_item":
                mid = await memory_svc.store_briefing_memory(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    text=text,
                    source=source,
                )
            else:
                mid = await memory_svc.store_memory(
                    user_id=user_id,
                    fact_text=text,
                    memory_type=memory_type,
                    scope=scope,
                    entity_ids=linked_ids or [],
                    workspace_id=workspace_id,
                    ttl_days=ttl_days if ttl_days > 0 else None,
                    source=source,
                )
            await db.commit()

            # Best-effort entity extraction from the stored text so that
            # entities mentioned in chat (e.g. company names, people) are
            # captured in the knowledge graph.
            entity_ids: list[str] = []
            try:
                from src.services.world_model import WorldModel

                wm = WorldModel(
                    _shared._settings,
                    db,
                    embedding_service=_shared._services.extras.get("embedding_service"),
                    vector_store=_shared._services.vector_store,
                )
                entity_ids = await wm.extract_from_text(
                    text, user_id=user_id, workspace_id=workspace_id
                )
                if entity_ids:
                    await db.commit()
            except Exception:
                logger.debug("Entity extraction from memory text failed", exc_info=True)

            # Sync extracted entities + their relationships to Neo4j
            if entity_ids and _shared._settings and _shared._settings.neo4j_url:
                try:
                    from src.services.graph_sync import GraphSyncService

                    gs = GraphSyncService(_shared._settings, db)
                    await gs.batch_sync_entities(entity_ids)
                    await gs.close()
                except Exception:
                    logger.debug(
                        "Neo4j sync after store_memory entity extraction failed",
                        exc_info=True,
                    )

            await ctx.info(f"Stored {memory_type} memory: {text[:80]}")
            return {
                "status": "stored",
                "memory_id": mid,
                "entity_ids": entity_ids,
            }
        except Exception as e:
            logger.error("store_memory failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.resource("entities://{workspace_id}/recent")
async def recent_entities_resource(workspace_id: str) -> str:
    """Recent entities from the world model."""
    import json

    async with _get_db() as db:
        from src.models.entities import Entity

        result = await db.execute(
            select(Entity)
            .where(Entity.workspace_id == workspace_id)
            .order_by(Entity.updated_at.desc())
            .limit(20)
        )
        entities = result.scalars().all()
        return json.dumps(
            [
                {
                    "entity_id": e.entity_id,
                    "name": e.canonical_name,
                    "type": e.entity_type,
                }
                for e in entities
            ]
        )
