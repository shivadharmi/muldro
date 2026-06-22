"""ConnectorPoller — connector polling, raw-event ingest, and cursor I/O.

Extracted from ``PerceptionRunner`` (orchestrator decomposition, 2026-06-19) to
keep that class under the 800-line cap. Owns the connector-facing half of the
perception cycle: polling a native connector for new events, ingesting them into
``normalized_events`` via ``EventProcessor``, and advancing the observation
cursor. The cursor upsert is deliberately folded into the ingest session so the
invariant "events ingested ⟹ cursor advanced" stays a single gated commit.

Depends downward on settings, the service container (for per-request services),
``EventPublisher`` (event bus for the EventProcessor), and the db-factory
provider — never on the chat path or the Planner. PerceptionRunner composes this
collaborator and owns the cycle/synthesis/queue/policy orchestration on top.
"""

import asyncio
import logging

from ulid import ULID

from src.config.settings import Settings
from src.orchestrator.event_publisher import EventPublisher
from src.orchestrator.services import ServiceContainer

logger = logging.getLogger(__name__)

# Map mcp_errors.MCPErrorCode string values to a PollErrorClass so generic poll()
# exceptions route through error_class_to_policy_error() and carry a classification
# keyword. Unmapped/unknown codes fall back to "transient" (fail-safe threshold 6) at
# the call site. Auth-related exceptions are permanent: a confirmed auth failure thrown
# from the connector won't self-heal on retry.
_MCP_CODE_TO_POLL_CLASS: dict[str, str] = {
    "auth_error": "permanent",
    "timeout": "transient",
    "rate_limit": "rate_limited",
    "server_error": "transient",
    "validation_error": "permanent",
    "circuit_open": "transient",
    "not_found": "permanent",
    "unknown_error": "transient",
}


class ConnectorPoller:
    """Polls connectors, ingests raw events, and advances observation cursors."""

    def __init__(
        self,
        settings: Settings,
        services: ServiceContainer | None,
        db_factory_provider,
        events: EventPublisher,
    ):
        self._settings = settings
        self._services = services
        # Provider (not a captured value) so reassigning db_factory on the
        # orchestrator propagates to this collaborator.
        self._db_factory_provider = db_factory_provider
        self._events = events

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    def _request_services(self, db) -> ServiceContainer:
        """Return a ServiceContainer whose DB-bound services use ``db``."""
        from src.runtime import request_services

        return request_services(self._services, self._settings, db)

    async def poll(
        self, source: str, user_id: str, workspace_id: str
    ) -> tuple[list, str | None, str | None, str]:
        """Poll a connector for new events. Returns (events, new_cursor, error, cursor_type)."""
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.connectors.poll_result import error_class_to_policy_error
        from src.services.oauth_manager import OAuthManager

        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            # Unregistered source is a config error — it never self-heals on retry.
            # Classify as permanent so the circuit opens immediately (threshold 1)
            # instead of falling through to the unknown/threshold-3 bucket.
            permanent_err = error_class_to_policy_error("permanent")
            return (
                [],
                None,
                f"No connector registered for source: {source} ({permanent_err})",
                "opaque",
            )

        connector = connector_cls(settings=self._settings)
        cursor_type = connector.cursor_type

        # Get OAuth credentials
        oauth_mgr = OAuthManager(
            self._db_factory,
            encryption_key=self._settings.oauth_encryption_key,
            settings=self._settings,
        )
        # Map source to OAuth provider (gmail/calendar share "google" provider)
        oauth_provider = "google" if source in ("gmail", "calendar") else source
        token_result = await oauth_mgr.get_valid_token_with_reason(user_id, oauth_provider)
        if token_result.token is None:
            # Distinguish a genuine token-refresh blip (transient — retry) from a
            # confirmed "no usable credential" (never connected / no refresh token /
            # revoked). The latter cannot self-heal by retrying, so classify it
            # auth_failed (-> permanent, threshold 1): the circuit opens fast and
            # re-authorization can be surfaced, instead of looping forever on a
            # source the user never connected. A live provider 401/403 still
            # surfaces as PollResult.auth_failed on the connector return path.
            from src.connectors.poll_result import (
                CREDENTIAL_ACQUISITION_ERROR,
                error_class_to_policy_error,
            )

            if token_result.reason == "refresh_failed":
                err = CREDENTIAL_ACQUISITION_ERROR
            else:  # no_token | no_refresh_token | revoked
                err = error_class_to_policy_error("auth_failed")
            return (
                [],
                None,
                f"No valid credentials for {source} — {err}",
                cursor_type,
            )
        access_token = token_result.token

        # Get current cursor
        cursor = None
        async with self._db_factory() as db:
            from sqlalchemy import select

            from src.models.observation_cursor import ObservationCursor

            result = await db.execute(
                select(ObservationCursor.cursor_value).where(
                    ObservationCursor.workspace_id == workspace_id,
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            row = result.first()
            if row:
                cursor = row[0]

        try:
            from src.connectors.poll_result import error_class_to_policy_error

            # Every registered connector returns a PollResult. On any failure it
            # carries empty events + the incoming cursor unchanged (fail -> empty +
            # unchanged cursor), so we never ingest events nor advance the cursor on
            # a failing poll.
            result = await asyncio.wait_for(
                connector.poll(user_id, cursor, {"access_token": access_token}),
                timeout=30,
            )

            if result.failed:
                # Sentinel message contains the keyword classify_error() needs;
                # prefix with source for observability without repeating error_class.
                policy_err = error_class_to_policy_error(result.error_class)
                error_msg = f"Poll failed for {source}: {policy_err}"
                logger.warning(
                    "connector_poll_error",
                    extra={
                        "source": source,
                        "error_class": result.error_class,
                        "error": error_msg[:500],
                    },
                )
                # Return unchanged cursor — never advance on failure
                return [], result.cursor, error_msg, cursor_type
            return result.events, result.cursor, None, cursor_type

        except asyncio.TimeoutError:
            logger.warning(
                "Connector %s poll timed out after 30s for user %s",
                source,
                user_id,
            )
            return [], cursor, "Poll timed out after 30s", cursor_type
        except Exception as e:
            from src.integrations.mcp_errors import classify_error

            error_code = classify_error(e)
            # mcp_errors codes (auth_error/server_error/unknown_error/...) don't
            # carry the keywords perception_policy.classify_error() greps for, so
            # without translation every exception bucketed as unknown (threshold 3).
            # Map to a PollErrorClass and route through error_class_to_policy_error
            # so the failure carries a classification keyword. A truly unknown
            # exception is treated as transient (threshold 6) — fail safe, never
            # open the circuit fast on an under-classified blip.
            poll_class = _MCP_CODE_TO_POLL_CLASS.get(error_code, "transient")
            policy_err = error_class_to_policy_error(poll_class)
            logger.warning(
                "connector_poll_error",
                extra={
                    "source": source,
                    "error_code": error_code,
                    "poll_class": poll_class,
                    "error": str(e)[:500],
                },
            )
            return (
                [],
                None,
                f"Poll failed for {source} ({error_code}: {policy_err}): {e}",
                cursor_type,
            )

    @staticmethod
    def build_cursor_upsert_stmt(
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str,
        cursor_type: str,
    ):
        """Return a pg ``INSERT … ON CONFLICT DO UPDATE`` statement for the
        observation cursor.  Both the ingest path and the empty-poll path use
        this builder so the SQL shape is never duplicated.

        The caller is responsible for executing the statement on its own
        ``db`` session; this function performs no I/O.
        """
        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.models.observation_cursor import ObservationCursor

        now = datetime.now(timezone.utc)
        return (
            pg_insert(ObservationCursor)
            .values(
                cursor_id=f"cur_{ULID()}",
                user_id=user_id,
                workspace_id=workspace_id,
                source=source,
                cursor_type=cursor_type,
                cursor_value=new_cursor,
                last_observation_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_cursor_ws_user_source",
                set_={
                    "cursor_value": new_cursor,
                    "cursor_type": cursor_type,
                    "last_observation_at": now,
                },
            )
        )

    async def ingest_raw_events(
        self,
        raw_events: list,
        user_id: str,
        workspace_id: str,
        *,
        source: str = "",
        new_cursor: str | None = None,
        cursor_type: str = "opaque",
    ) -> list[str]:
        """Ingest raw events into the event processor. Returns summary strings.

        ``EventProcessor.process()`` commits **per event** internally, so by
        the time the loop finishes the session may have issued many commits.
        When *new_cursor* is also provided, the cursor upsert is executed on
        the **same** session after the loop and committed by the single trailing
        ``await db.commit()`` at the end of this method.

        The invariant guaranteed here is narrower than a single transaction:
        **the cursor is not advanced unless the event loop ran to completion**
        (i.e. no ``new_cursor`` write happens if the session or
        ``EventProcessor`` construction raises before the loop starts).
        Per-event commit failures are caught and forwarded to the DLQ; they do
        not prevent the cursor from advancing for the events that succeeded.
        """
        summaries = []
        async with self._db_factory() as db:
            from src.services.dead_letter import DeadLetterService
            from src.services.event_processor import EventProcessor

            req = self._request_services(db)
            event_bus = await self._events.ensure_event_bus()
            dead_letter = DeadLetterService(db)

            processor = EventProcessor(
                self._settings,
                db,
                world_model=req.world_model,
                memory_service=req.memory_service,
                dead_letter=dead_letter,
                event_bus=event_bus,
                notifier=req.notifier,
                embedding_service=req.extras.get("embedding_service"),
                vector_store=req.vector_store,
            )
            for raw in raw_events:
                try:
                    # Ingest the event (persists to normalized_events with its
                    # own event_id). The returned id is intentionally not woven
                    # into the human-readable summary below — see comment there.
                    await processor.process(
                        raw,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                    title = raw.title or getattr(raw, "raw_data", {}).get("subject", "")
                    # Agent-facing observation line. Carries source/type/subject so
                    # the Librarian/Planner can reason about it, but NOT the internal
                    # event_id ULID — that leaks into user-facing surface titles and
                    # briefing memory. event_id is persisted in normalized_events.
                    summary = f"[{raw.source}] {raw.event_type}: {title}"
                    summaries.append(summary)
                except Exception as e:
                    await db.rollback()
                    logger.warning(
                        "event_ingest_failed",
                        extra={
                            "source": raw.source,
                            "event_type": raw.event_type,
                            "error": str(e)[:500],
                        },
                    )
                    summaries.append(f"[{raw.source}] {raw.event_type} (ingest error)")
                    try:
                        await dead_letter.enqueue(
                            user_id=user_id,
                            operation_type="event_ingest",
                            error_type=type(e).__name__,
                            error_message=str(e),
                            source_id=raw.entity_id,
                            payload={
                                "source": raw.source,
                                "event_type": raw.event_type,
                                "entity_id": raw.entity_id,
                            },
                            workspace_id=workspace_id,
                        )
                    except Exception:
                        logger.debug("DLQ enqueue failed", exc_info=True)

            # Advance the cursor on the same session so it is not written
            # unless the event loop ran to completion.
            if new_cursor and source:
                stmt = self.build_cursor_upsert_stmt(
                    source, user_id, workspace_id, new_cursor, cursor_type
                )
                await db.execute(stmt)
            elif new_cursor and not source:
                logger.warning(
                    "ingest_cursor_skipped_no_source",
                    extra={"new_cursor": new_cursor, "user_id": user_id},
                )

            await db.commit()
        return summaries

    async def update_cursor(
        self,
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str | None,
        cursor_type: str = "opaque",
    ) -> None:
        """Update the observation cursor after a successful poll.

        Uses a single ``INSERT ... ON CONFLICT DO UPDATE`` so concurrent
        perception cycles for the same ``(workspace_id, user_id, source)``
        cannot race on the ``uq_cursor_ws_user_source`` unique constraint.

        This method is used by the **empty-poll path** so incremental sync
        tokens (e.g. Gmail historyId, Calendar syncToken) advance even when
        no new events were returned.  The non-empty-poll path folds the cursor
        write directly into ``ingest_raw_events`` instead.
        """
        if not new_cursor:
            return
        async with self._db_factory() as db:
            stmt = self.build_cursor_upsert_stmt(
                source, user_id, workspace_id, new_cursor, cursor_type
            )
            await db.execute(stmt)
            await db.commit()
