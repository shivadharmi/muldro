"""Intelligence MCP server — wraps existing Jarvis services as MCP tools.

Built with FastMCP. Provides tools for event ingestion, memory search,
entity management, planning, policy evaluation, briefings, cursors, and approvals.
These are the internal tools that Jarvis sub-agents use to interact with
the intelligence layer.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastmcp import Context, FastMCP
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.integrations.mcp_errors import make_error_response
from src.models.approvals import Approval
from src.models.observation_cursor import ObservationCursor

logger = logging.getLogger(__name__)

intelligence = FastMCP("jarvis-intelligence")

# We store a reference to the DB session factory and settings that gets set
# at orchestrator startup. Tools access these via the module-level variables.
_db_factory = None
_settings = None
_services = None


def configure(db_factory, settings, services):
    """Configure the intelligence server with runtime dependencies.

    Called once during orchestrator startup. Services should be a ServiceContainer.
    """
    global _db_factory, _settings, _services
    _db_factory = db_factory
    _settings = settings
    _services = services


@asynccontextmanager
async def _get_db() -> AsyncGenerator[AsyncSession, None]:
    if _db_factory is None:
        raise RuntimeError("Intelligence server not configured. Call configure() first.")
    async with _db_factory() as session:
        yield session


# ── Event Ingestion ──────────────────────────────────────────────────────


@intelligence.tool(
    tags={"observer", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def ingest_event(
    user_id: str,
    source: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    title: str,
    ctx: Context,
    summary: str = "",
    actor_email: str = "",
    actor_name: str = "",
    occurred_at: str = "",
    raw_payload: str = "",
    workspace_id: str = "",
) -> dict:
    """Ingest an event into the Jarvis intelligence pipeline.

    Normalizes, scores importance/urgency, deduplicates, and triggers
    entity extraction + memory extraction + proactive planning.
    """
    from src.services.event_processor import RawEvent

    async with _get_db() as db:
        try:
            actor = {}
            if actor_email or actor_name:
                actor = {"email": actor_email, "name": actor_name, "type": "person"}

            ts = datetime.now(timezone.utc)
            if occurred_at:
                try:
                    ts = datetime.fromisoformat(occurred_at)
                except ValueError:
                    pass

            raw = RawEvent(
                source=source,
                source_account_id=f"{source}_primary",
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                occurred_at=ts,
                title=title,
                summary=summary,
                actor=actor if actor else None,
                raw_payload=None,
            )

            processor = _services.event_processor
            result = await processor.process(user_id, raw, workspace_id=workspace_id)
            await db.commit()

            await ctx.info(f"Ingested event from {source}: {title}")
            return {
                "status": "ingested",
                "event_id": result.get("event_id"),
                "importance_score": result.get("importance_score"),
            }
        except Exception as e:
            logger.error("ingest_event failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


# ── Unified Search ──────────────────────────────────────────────────────


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
            svc = _services
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


# ── Entity Management ────────────────────────────────────────────────────


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

                alias = EntityAlias(
                    alias_id=f"alias_{ULID()}",
                    entity_id=entity_id,
                    alias_value=add_alias,
                )
                db.add(alias)

            await db.flush()
            await db.commit()
            return {"status": "updated", "entity_id": entity_id}
        except Exception as e:
            logger.error("update_entity failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


# ── Planning ─────────────────────────────────────────────────────────────


@intelligence.tool(
    tags={"planner", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_active_plans(
    user_id: str,
    ctx: Context,
    limit: int = 10,
    workspace_id: str = "",
) -> dict:
    """Get currently active plans (not completed/failed/cancelled)."""
    async with _get_db() as db:
        try:
            from src.models.plans import Plan

            result = await db.execute(
                select(Plan)
                .where(Plan.user_id == user_id)
                .where(Plan.workspace_id == workspace_id)
                .where(Plan.status.notin_(["completed", "failed", "cancelled"]))
                .order_by(Plan.created_at.desc())
                .limit(limit)
            )
            plans = result.scalars().all()
            return {
                "plans": [
                    {
                        "plan_id": p.plan_id,
                        "goal": p.goal,
                        "priority": p.priority,
                        "status": p.status,
                        "decision": p.decision,
                    }
                    for p in plans
                ],
                "count": len(plans),
            }
        except Exception as e:
            logger.error("get_active_plans failed: %s", e, exc_info=True)
            return {"plans": [], "count": 0, "error": str(e)}


# ── Policy Evaluation ────────────────────────────────────────────────────


@intelligence.tool(
    tags={"governor", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def evaluate_policy(
    user_id: str,
    plan_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Evaluate governance policy for a plan.

    Returns: auto_execute, approval_required, or blocked — with reasoning.
    """
    async with _get_db():
        try:
            governor = _services.governor
            result = await governor.evaluate_plan(plan_id, user_id, workspace_id=workspace_id)
            return result.model_dump()
        except Exception as e:
            logger.error("evaluate_policy failed: %s", e, exc_info=True)
            return make_error_response(e)


# ── Approvals ────────────────────────────────────────────────────────────


@intelligence.tool(
    tags={"governor", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
async def approve_action(
    user_id: str,
    approval_id: str,
    decision: str,
    ctx: Context,
    reason: str = "",
    workspace_id: str = "",
) -> dict:
    """Approve or reject a pending action.

    decision: 'approved' or 'rejected'
    """
    async with _get_db() as db:
        try:
            result = await db.execute(
                select(Approval).where(
                    Approval.approval_id == approval_id,
                    Approval.workspace_id == workspace_id,
                )
            )
            approval = result.scalar_one_or_none()
            if not approval:
                return {"status": "not_found"}
            if approval.status != "pending":
                return {"status": "already_decided", "current_status": approval.status}

            approval.status = decision
            approval.decided_at = datetime.now(timezone.utc)
            approval.decision_reason = reason
            await db.commit()

            # Log to audit
            audit = _services.audit
            if audit:
                await audit.log(
                    user_id=user_id,
                    action_type=f"approval_{decision}",
                    summary=f"Approval {approval_id} {decision}: {reason}",
                    approval_id=approval_id,
                    policy_decision=decision,
                )

            await ctx.info(f"Approval {approval_id} {decision}")
            return {"status": decision, "approval_id": approval_id}
        except Exception as e:
            logger.error("approve_action failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


# ── Preference Extraction ────────────────────────────────────────────────


@intelligence.tool(
    tags={"persona", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def extract_preferences(
    user_id: str,
    source_text: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Extract user preferences from interaction text.

    The Persona agent calls this to store learned preferences as memories.
    source_text: description of the interaction to analyze
    """
    async with _get_db():
        try:
            memory_service = _services.memory_service
            if not memory_service:
                return {"status": "error", "error": "Memory service not available"}

            memory_ids = await memory_service.extract_preferences(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[],
                workspace_id=workspace_id,
            )
            return {
                "status": "ok",
                "memories_created": len(memory_ids),
                "memory_ids": memory_ids,
            }
        except Exception as e:
            logger.error("extract_preferences failed: %s", e, exc_info=True)
            return make_error_response(e)


# ── Briefing ─────────────────────────────────────────────────────────────


@intelligence.tool(
    tags={"presenter", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_briefing(
    user_id: str,
    ctx: Context,
    date: str = "today",
    workspace_id: str = "",
) -> dict:
    """Generate or fetch the daily briefing.

    date: 'today' or ISO date string (YYYY-MM-DD)
    """
    async with _get_db():
        try:
            from datetime import date as date_type

            await ctx.report_progress(0, 3, "Loading briefing data...")
            briefing_date = date_type.today() if date == "today" else date_type.fromisoformat(date)
            presenter = _services.presenter
            await ctx.report_progress(1, 3, "Generating briefing...")
            briefing = await presenter.generate_briefing(
                user_id, briefing_date, workspace_id=workspace_id
            )
            await ctx.report_progress(3, 3, "Briefing ready")
            return {
                "status": "ok",
                "briefing_id": briefing.briefing_id,
                "briefing_date": str(briefing.briefing_date),
                "headline": briefing.headline,
                "top_priorities": briefing.top_priorities,
                "changes_since_last": briefing.changes_since_last,
                "pending_approvals": briefing.pending_approvals,
                "recommended_actions": briefing.recommended_actions,
                "full_text": briefing.full_text,
            }
        except Exception as e:
            logger.error("get_briefing failed: %s", e, exc_info=True)
            return make_error_response(e)


# ── Observation Cursors ──────────────────────────────────────────────────


@intelligence.tool(
    tags={"observer", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_observation_cursor(
    user_id: str,
    source: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Get the last observation checkpoint for a data source.

    source: gmail, calendar, slack, github
    Returns cursor_value (or null if no previous observation).
    """
    async with _get_db() as db:
        try:
            result = await db.execute(
                select(ObservationCursor).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.workspace_id == workspace_id,
                    ObservationCursor.source == source,
                )
            )
            cursor = result.scalar_one_or_none()
            if cursor:
                return {
                    "source": source,
                    "cursor_type": cursor.cursor_type,
                    "cursor_value": cursor.cursor_value,
                    "last_observation_at": cursor.last_observation_at.isoformat(),
                }
            return {"source": source, "cursor_value": None}
        except Exception as e:
            logger.error("get_observation_cursor failed: %s", e, exc_info=True)
            return {"source": source, "cursor_value": None, "error": str(e)}


@intelligence.tool(
    tags={"observer", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def update_observation_cursor(
    user_id: str,
    source: str,
    cursor_type: str,
    cursor_value: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Update the observation checkpoint after a successful observation cycle.

    source: gmail, calendar, slack, github
    cursor_type: last_history_id, sync_token, oldest_ts, since_timestamp
    cursor_value: The actual cursor/checkpoint value
    """
    async with _get_db() as db:
        try:
            result = await db.execute(
                select(ObservationCursor).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.workspace_id == workspace_id,
                    ObservationCursor.source == source,
                )
            )
            cursor = result.scalar_one_or_none()

            if cursor:
                cursor.cursor_type = cursor_type
                cursor.cursor_value = cursor_value
                cursor.last_observation_at = datetime.now(timezone.utc)
            else:
                cursor = ObservationCursor(
                    cursor_id=f"cursor_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source=source,
                    cursor_type=cursor_type,
                    cursor_value=cursor_value,
                    last_observation_at=datetime.now(timezone.utc),
                )
                db.add(cursor)

            await db.flush()
            await db.commit()
            return {"status": "updated", "source": source, "cursor_value": cursor_value}
        except Exception as e:
            logger.error("update_observation_cursor failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


# ── Observation Reporting ────────────────────────────────────────────────


@intelligence.tool(
    tags={"observer", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def report_observation(
    user_id: str,
    source: str,
    ctx: Context,
    items_found: int = 0,
    items_ingested: int = 0,
    status: str = "ok",
    error_message: str = "",
    workspace_id: str = "",
) -> dict:
    """Report the results of an observation cycle for health tracking.

    Writes to perception_state (consolidated from legacy observation_status).
    """
    async with _get_db() as db:
        try:
            from src.models.perception_state import PerceptionState

            result = await db.execute(
                select(PerceptionState).where(
                    PerceptionState.user_id == user_id,
                    PerceptionState.workspace_id == workspace_id,
                    PerceptionState.source == source,
                )
            )
            ps = result.scalar_one_or_none()
            now = datetime.now(timezone.utc)
            circuit = "open" if status == "error" else "closed"

            if ps:
                ps.last_run_at = now
                ps.last_event_count = items_found
                ps.circuit_state = circuit
                ps.last_error = error_message if error_message else None
                if status == "error":
                    ps.consecutive_failures += 1
                else:
                    ps.consecutive_failures = 0
                ps.total_runs += 1
            else:
                from src.models.ids import generate_id

                ps = PerceptionState(
                    state_id=generate_id("pst"),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source=source,
                    last_run_at=now,
                    last_event_count=items_found,
                    circuit_state=circuit,
                    last_error=error_message if error_message else None,
                    consecutive_failures=1 if status == "error" else 0,
                    total_runs=1,
                )
                db.add(ps)

            await db.flush()
            await db.commit()
            return {"status": "reported", "source": source}
        except Exception as e:
            logger.error("report_observation failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


# ── Execution Tracking ───────────────────────────────────────────────────


@intelligence.tool(
    tags={"operator", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def update_execution(
    execution_id: str,
    status: str,
    ctx: Context,
    user_id: str = "",
    result_summary: str = "",
    error_message: str = "",
    workspace_id: str = "",
) -> dict:
    """Update the status of an execution.

    status: running, completed, failed
    """
    async with _get_db() as db:
        try:
            from src.models.task_graph import TaskRun

            result = await db.execute(
                select(TaskRun).where(
                    TaskRun.run_id == execution_id,
                    TaskRun.workspace_id == workspace_id,
                )
            )
            run = result.scalar_one_or_none()
            if not run:
                return {"status": "not_found"}

            from src.services.execution_state import InvalidTransitionError, transition_run

            try:
                transition_run(run, status)
            except InvalidTransitionError as e:
                return make_error_response(e)
            if status == "completed":
                run.completed_at = datetime.now(timezone.utc)
            if error_message:
                run.error = {"message": error_message}

            await db.flush()
            await db.commit()
            return {
                "status": "updated",
                "run_id": execution_id,
                "new_status": status,
            }
        except Exception as e:
            logger.error("update_execution failed: %s", e, exc_info=True)
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
    async with _get_db():
        try:
            from src.services.context_builder import ContextBuilder

            await ctx.report_progress(0, 4, "Initializing context builder...")
            builder = ContextBuilder(
                world_model=_services.world_model,
                memory_service=_services.memory_service,
                artifact_store=_services.artifact_store,
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
    tags={"operator", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def verify_run(
    run_id: str,
    ctx: Context,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Verify a completed run against success conditions.

    Returns verdict (passed/failed/partial/skipped) and details.
    """
    async with _get_db() as db:
        try:
            from src.models.plans import Plan
            from src.models.task_graph import TaskRun
            from src.services.verifier import Verifier

            run_result = await db.execute(
                select(TaskRun).where(
                    TaskRun.run_id == run_id,
                    TaskRun.workspace_id == workspace_id,
                )
            )
            run = run_result.scalar_one_or_none()
            if not run:
                return {
                    "verdict": "skipped",
                    "details": "Run not found",
                }

            conditions = None
            if run.plan_id:
                plan_result = await db.execute(
                    select(Plan).where(
                        Plan.plan_id == run.plan_id,
                        Plan.workspace_id == workspace_id,
                    )
                )
                plan = plan_result.scalar_one_or_none()
                if plan:
                    conditions = getattr(plan, "success_conditions", None)

            verifier = Verifier(_settings, db)
            result = await verifier.verify_run(run_id, conditions)
            return {
                "verdict": result.verdict.value,
                "score": result.score,
                "details": result.details,
                "checks_passed": result.checks_passed,
                "checks_failed": result.checks_failed,
            }
        except Exception as e:
            logger.error("verify_run failed: %s", e, exc_info=True)
            return {"verdict": "skipped", "error": str(e)}


# ── MCP Resources — Live Data ───────────────────────────────────────────


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


@intelligence.resource("plans://{workspace_id}/active")
async def active_plans_resource(workspace_id: str) -> str:
    """Currently active plans."""
    import json

    async with _get_db() as db:
        from src.models.plans import Plan

        result = await db.execute(
            select(Plan)
            .where(
                Plan.workspace_id == workspace_id,
                Plan.status.notin_(["completed", "failed", "cancelled"]),
            )
            .order_by(Plan.created_at.desc())
            .limit(10)
        )
        plans = result.scalars().all()
        return json.dumps(
            [
                {
                    "plan_id": p.plan_id,
                    "goal": p.goal,
                    "priority": p.priority,
                    "status": p.status,
                }
                for p in plans
            ]
        )
