"""Intelligence MCP server — wraps existing Jarvis services as MCP tools.

Built with FastMCP. Provides tools for event ingestion, memory search,
entity management, planning, policy evaluation, briefings, cursors, and approvals.
These are the internal tools that Jarvis sub-agents use to interact with
the intelligence layer.
"""

import logging
from datetime import datetime, timezone

from fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.approvals import Approval
from src.models.observation_cursor import ObservationCursor

logger = logging.getLogger(__name__)

intelligence = FastMCP("jarvis-intelligence")

# We store a reference to the DB session factory and settings that gets set
# at orchestrator startup. Tools access these via the module-level variables.
_db_factory = None
_settings = None
_services = None


def configure(db_factory, settings, services: dict):
    """Configure the intelligence server with runtime dependencies.

    Called once during orchestrator startup. Services dict should contain:
    event_processor, world_model, memory_service, planner, governor, presenter, audit
    """
    global _db_factory, _settings, _services
    _db_factory = db_factory
    _settings = settings
    _services = services


async def _get_db() -> AsyncSession:
    if _db_factory is None:
        raise RuntimeError("Intelligence server not configured. Call configure() first.")
    return _db_factory()


# ── Event Ingestion ──────────────────────────────────────────────────────


@intelligence.tool()
async def ingest_event(
    source: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    title: str,
    summary: str = "",
    actor_email: str = "",
    actor_name: str = "",
    occurred_at: str = "",
    raw_payload: str = "",
) -> dict:
    """Ingest an event into the Jarvis intelligence pipeline.

    Normalizes, scores importance/urgency, deduplicates, and triggers
    entity extraction + memory extraction + proactive planning.
    """
    from src.services.event_processor import RawEvent

    db = await _get_db()
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

        processor = _services["event_processor"]
        result = await processor.process("usr_default", raw)
        await db.commit()

        return {
            "status": "ingested",
            "event_id": result.get("event_id"),
            "importance_score": result.get("importance_score"),
        }
    except Exception as e:
        logger.error("ingest_event failed: %s", e, exc_info=True)
        await db.rollback()
        return {"status": "error", "error": str(e)}


# ── Memory Search ────────────────────────────────────────────────────────


@intelligence.tool()
async def search_memory(
    query: str,
    scope: str = "all",
    memory_type: str = "",
    limit: int = 10,
) -> dict:
    """Search Jarvis's knowledge: memories, entities, events.

    Uses pgvector semantic search + keyword matching.
    scope: all, memory, entities, events
    memory_type: episodic, semantic, preference, relationship, task_context (optional filter)
    """
    await _get_db()  # Ensure configured
    try:
        memory_svc = _services["memory_service"]
        memory_types = [memory_type] if memory_type else None
        results = await memory_svc.retrieve(
            "usr_default",
            query,
            memory_types=memory_types,
            max_results=limit,
        )
        return {
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        logger.error("search_memory failed: %s", e, exc_info=True)
        return {"results": [], "count": 0, "error": str(e)}


# ── Entity Management ────────────────────────────────────────────────────


@intelligence.tool()
async def get_entities(
    query: str = "",
    entity_type: str = "",
    limit: int = 20,
) -> dict:
    """Get entities from the world model.

    Optionally filter by query (name search) and entity_type (person, organization, project).
    """
    db = await _get_db()
    try:
        world_model = _services["world_model"]
        if query:
            entities = await world_model.find_entity("usr_default", query)
            return {"entities": [entities] if entities else [], "count": 1 if entities else 0}
        # List recent entities
        from src.models.entities import Entity

        stmt = select(Entity).where(Entity.user_id == "usr_default")
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        stmt = stmt.order_by(Entity.updated_at.desc()).limit(limit)
        result = await db.execute(stmt)
        entities = result.scalars().all()
        return {
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "name": e.canonical_name,
                    "type": e.entity_type,
                    "attributes": e.attributes,
                }
                for e in entities
            ],
            "count": len(entities),
        }
    except Exception as e:
        logger.error("get_entities failed: %s", e, exc_info=True)
        return {"entities": [], "count": 0, "error": str(e)}


@intelligence.tool()
async def update_entity(
    entity_id: str,
    attributes: str = "",
    add_alias: str = "",
) -> dict:
    """Update an entity's attributes or add an alias."""
    db = await _get_db()
    try:
        from src.models.entities import Entity

        result = await db.execute(select(Entity).where(Entity.entity_id == entity_id))
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
        return {"status": "error", "error": str(e)}


# ── Planning ─────────────────────────────────────────────────────────────


@intelligence.tool()
async def plan_command(command: str, context: str = "") -> dict:
    """Process a natural language command through the Jarvis planner.

    Returns a structured task graph with decision, priority, risk level, and tasks.
    """
    await _get_db()  # Ensure configured
    try:
        planner = _services["planner"]
        result = await planner.plan_for_command("usr_default", command, context=context)
        return result
    except Exception as e:
        logger.error("plan_command failed: %s", e, exc_info=True)
        return {"status": "error", "decision": "error", "error": str(e)}


@intelligence.tool()
async def get_active_plans(limit: int = 10) -> dict:
    """Get currently active plans (not completed/failed/cancelled)."""
    db = await _get_db()
    try:
        from src.models.plans import Plan

        result = await db.execute(
            select(Plan)
            .where(Plan.user_id == "usr_default")
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


@intelligence.tool()
async def evaluate_policy(
    plan_id: str,
) -> dict:
    """Evaluate governance policy for a plan.

    Returns: auto_execute, approval_required, or blocked — with reasoning.
    """
    await _get_db()  # Ensure configured
    try:
        governor = _services["governor"]
        result = await governor.evaluate_plan("usr_default", plan_id)
        return result
    except Exception as e:
        logger.error("evaluate_policy failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ── Approvals ────────────────────────────────────────────────────────────


@intelligence.tool()
async def approve_action(approval_id: str, decision: str, reason: str = "") -> dict:
    """Approve or reject a pending action.

    decision: 'approved' or 'rejected'
    """
    db = await _get_db()
    try:
        result = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
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
        audit = _services.get("audit")
        if audit:
            await audit.log(
                user_id="usr_default",
                action_type=f"approval_{decision}",
                summary=f"Approval {approval_id} {decision}: {reason}",
                approval_id=approval_id,
                policy_decision=decision,
            )

        return {"status": decision, "approval_id": approval_id}
    except Exception as e:
        logger.error("approve_action failed: %s", e, exc_info=True)
        await db.rollback()
        return {"status": "error", "error": str(e)}


# ── Preference Extraction ────────────────────────────────────────────────


@intelligence.tool()
async def extract_preferences(source_text: str) -> dict:
    """Extract user preferences from interaction text.

    The Persona agent calls this to store learned preferences as memories.
    source_text: description of the interaction to analyze
    """
    await _get_db()
    try:
        memory_service = _services.get("memory_service")
        if not memory_service:
            return {"status": "error", "error": "Memory service not available"}

        memory_ids = await memory_service.extract_preferences(
            user_id="usr_default",
            source_text=source_text,
            source_event_ids=[],
        )
        return {"status": "ok", "memories_created": len(memory_ids), "memory_ids": memory_ids}
    except Exception as e:
        logger.error("extract_preferences failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ── Briefing ─────────────────────────────────────────────────────────────


@intelligence.tool()
async def get_briefing(date: str = "today") -> dict:
    """Generate or fetch the daily briefing.

    date: 'today' or ISO date string (YYYY-MM-DD)
    """
    await _get_db()  # Ensure configured
    try:
        presenter = _services["presenter"]
        result = await presenter.generate_briefing("usr_default")
        return result
    except Exception as e:
        logger.error("get_briefing failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ── Observation Cursors ──────────────────────────────────────────────────


@intelligence.tool()
async def get_observation_cursor(source: str) -> dict:
    """Get the last observation checkpoint for a data source.

    source: gmail, calendar, slack, github
    Returns cursor_value (or null if no previous observation).
    """
    db = await _get_db()
    try:
        result = await db.execute(
            select(ObservationCursor).where(
                ObservationCursor.user_id == "usr_default",
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


@intelligence.tool()
async def update_observation_cursor(
    source: str,
    cursor_type: str,
    cursor_value: str,
) -> dict:
    """Update the observation checkpoint after a successful observation cycle.

    source: gmail, calendar, slack, github
    cursor_type: last_history_id, sync_token, oldest_ts, since_timestamp
    cursor_value: The actual cursor/checkpoint value
    """
    db = await _get_db()
    try:
        result = await db.execute(
            select(ObservationCursor).where(
                ObservationCursor.user_id == "usr_default",
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
                user_id="usr_default",
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
        return {"status": "error", "error": str(e)}


# ── Observation Reporting ────────────────────────────────────────────────


@intelligence.tool()
async def report_observation(
    source: str,
    items_found: int = 0,
    items_ingested: int = 0,
    status: str = "ok",
    error_message: str = "",
) -> dict:
    """Report the results of an observation cycle for health tracking."""
    db = await _get_db()
    try:
        from src.models.observation import ObservationStatus

        result = await db.execute(
            select(ObservationStatus).where(
                ObservationStatus.user_id == "usr_default",
                ObservationStatus.source == source,
            )
        )
        obs = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if obs:
            obs.last_observed_at = now
            obs.items_found = items_found
            obs.items_ingested = items_ingested
            obs.status = status
            obs.error_message = error_message if error_message else None
        else:
            obs = ObservationStatus(
                observation_id=f"obs_{ULID()}",
                user_id="usr_default",
                source=source,
                last_observed_at=now,
                items_found=items_found,
                items_ingested=items_ingested,
                status=status,
                error_message=error_message if error_message else None,
            )
            db.add(obs)

        await db.flush()
        await db.commit()
        return {"status": "reported", "source": source}
    except Exception as e:
        logger.error("report_observation failed: %s", e, exc_info=True)
        await db.rollback()
        return {"status": "error", "error": str(e)}


# ── Execution Tracking ───────────────────────────────────────────────────


@intelligence.tool()
async def update_execution(
    execution_id: str,
    status: str,
    result_summary: str = "",
    error_message: str = "",
) -> dict:
    """Update the status of an execution.

    status: running, completed, failed
    """
    db = await _get_db()
    try:
        from src.models.executions import Execution

        result = await db.execute(select(Execution).where(Execution.execution_id == execution_id))
        execution = result.scalar_one_or_none()
        if not execution:
            return {"status": "not_found"}

        execution.status = status
        if status == "completed":
            execution.completed_at = datetime.now(timezone.utc)
        if result_summary:
            execution.result_summary = result_summary
        if error_message:
            execution.error_message = error_message

        await db.flush()
        await db.commit()
        return {"status": "updated", "execution_id": execution_id, "new_status": status}
    except Exception as e:
        logger.error("update_execution failed: %s", e, exc_info=True)
        await db.rollback()
        return {"status": "error", "error": str(e)}


# ── Task Management ─────────────────────────────────────────────────


@intelligence.tool()
async def create_task(
    title: str,
    description: str = "",
    task_type: str = "general",
    priority: str = "medium",
    goal_id: str = "",
) -> dict:
    """Create a standalone task in the task system.

    task_type: general, draft_email, research, meeting_prep, etc.
    priority: low, medium, high, critical
    """
    db = await _get_db()
    try:
        from src.services.task_service import TaskService

        svc = TaskService(db)
        task = await svc.create_task(
            user_id="usr_default",
            title=title,
            description=description or None,
            task_type=task_type,
            priority=priority,
            goal_id=goal_id or None,
        )
        await db.commit()
        return {
            "status": "created",
            "task_id": task.task_id,
            "title": task.title,
        }
    except Exception as e:
        logger.error("create_task failed: %s", e, exc_info=True)
        await db.rollback()
        return {"status": "error", "error": str(e)}


@intelligence.tool()
async def get_task(task_id: str) -> dict:
    """Get details of a standalone task by ID."""
    db = await _get_db()
    try:
        from src.services.task_service import TaskService

        svc = TaskService(db)
        task = await svc.get_task(task_id, "usr_default")
        if not task:
            return {"status": "not_found", "task_id": task_id}
        return {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "task_type": task.task_type,
            "goal_id": task.goal_id,
        }
    except Exception as e:
        logger.error("get_task failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


@intelligence.tool()
async def get_goals(
    status: str = "active",
    limit: int = 10,
) -> dict:
    """Get user goals, optionally filtered by status."""
    await _get_db()
    try:
        goal_tracker = _services.get("goal_tracker")
        if not goal_tracker:
            return {
                "goals": [],
                "error": "Goal tracker not available",
            }
        goals = await goal_tracker.list_goals("usr_default", status=status)
        return {
            "goals": [
                {
                    "goal_id": g.goal_id,
                    "title": g.title,
                    "status": g.status,
                    "progress": g.progress,
                    "priority": getattr(g, "priority", "medium"),
                }
                for g in goals[:limit]
            ],
            "count": min(len(goals), limit),
        }
    except Exception as e:
        logger.error("get_goals failed: %s", e, exc_info=True)
        return {"goals": [], "error": str(e)}


@intelligence.tool()
async def build_context(
    query: str,
    task_type: str = "",
) -> dict:
    """Build a rich context pack for a query/task.

    Returns assembled context from entities, memories, goals,
    procedures, and artifacts.
    """
    await _get_db()
    try:
        from src.services.context_builder import ContextBuilder

        builder = ContextBuilder(
            world_model=_services.get("world_model"),
            memory_service=_services.get("memory_service"),
            goal_tracker=_services.get("goal_tracker"),
            procedure_library=_services.get("procedure_library"),
            artifact_store=_services.get("artifact_store"),
        )
        pack = await builder.build(
            "usr_default",
            query,
            task_type=task_type or None,
        )
        prompt_text = ContextBuilder.to_prompt(pack)
        return {
            "context_prompt": prompt_text,
            "entity_count": len(pack.entities),
            "goal_count": len(pack.goals),
            "memory_count": (len(pack.recent_events) + len(pack.preferences)),
        }
    except Exception as e:
        logger.error("build_context failed: %s", e, exc_info=True)
        return {"context_prompt": "", "error": str(e)}


@intelligence.tool()
async def verify_run(
    run_id: str,
) -> dict:
    """Verify a completed run against success conditions.

    Returns verdict (passed/failed/partial/skipped) and details.
    """
    db = await _get_db()
    try:
        from src.models.plans import Plan
        from src.models.task_graph import TaskRun
        from src.services.verifier import Verifier

        run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = run_result.scalar_one_or_none()
        if not run:
            return {
                "verdict": "skipped",
                "details": "Run not found",
            }

        conditions = None
        if run.plan_id:
            plan_result = await db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
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
