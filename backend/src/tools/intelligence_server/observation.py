"""Observation-domain MCP tools: event ingestion, cursors, cycle reporting."""

import logging
from datetime import datetime, timezone

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from sqlalchemy import select
from ulid import ULID

from src.integrations.mcp_errors import make_error_response
from src.models.observation_cursor import ObservationCursor
from src.tools.intelligence_server import _shared
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


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

            processor = _shared.request_services(db).event_processor
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
