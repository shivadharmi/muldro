"""PerceptionRunner — the perception + cross-source-synthesis engine.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
Owns the autonomous (scheduler-triggered) intelligence loop: polling connectors,
ingesting raw events, advancing observation cursors, assessing relevance, asking
the Planner to evaluate observations, and queuing actionable perception plans for
background execution.

Depends downward on AgentInvoker (running sub-agents), EventPublisher (event bus +
runtime events), SurfacePusher (insight surfaces), PlanStore (plan persistence), and
the SystemCapabilityHandler — never on the chat path, which is what keeps the
chat<->perception relationship acyclic.
"""

import asyncio
import logging

from ulid import ULID

from src.config.settings import Settings
from src.contracts import PlanOutput
from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.event_publisher import EventPublisher
from src.orchestrator.intent_classifier import extract_plan
from src.orchestrator.plan_store import PlanStore
from src.orchestrator.services import ServiceContainer
from src.orchestrator.surface_pusher import SurfacePusher
from src.orchestrator.tracing import TraceManager

logger = logging.getLogger(__name__)


async def _fetch_thread_contexts(
    raw_events: list,
    user_id: str,
    workspace_id: str,
    max_threads: int = 3,
) -> dict[str, dict]:
    """Fetch full thread context for Gmail reply events via MCP.

    When a reply arrives on a Gmail thread, the Librarian/Planner only see
    the reply snippet with no context about the prior conversation. This
    helper fetches the full thread via the ``get_gmail_thread_content`` MCP
    tool so downstream agents can reason over the complete thread.

    Returns a mapping of ``{thread_id: mcp_result}`` for successfully
    fetched threads. On any failure the thread is silently skipped so
    perception is never blocked by MCP availability.
    """
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    contexts: dict[str, dict] = {}
    if not is_mcp_tool("get_gmail_thread_content"):
        return contexts

    fetched = 0
    seen: set[str] = set()
    for raw_evt in raw_events:
        if fetched >= max_threads:
            break
        if raw_evt.source != "gmail":
            continue
        payload = raw_evt.raw_payload or {}
        in_reply_to = payload.get("in_reply_to", "")
        thread_id = raw_evt.entity_id
        if not in_reply_to or thread_id in seen:
            continue
        seen.add(thread_id)

        try:
            result = await call_mcp_tool(
                "get_gmail_thread_content",
                {"thread_id": thread_id},
                user_id=user_id,
                workspace_id=workspace_id,
            )
            if isinstance(result, dict) and result.get("status") != "error":
                contexts[thread_id] = result
                fetched += 1
        except Exception:
            logger.debug("Failed to fetch thread %s context", thread_id, exc_info=True)

    return contexts


class PerceptionRunner:
    """Runs perception cycles and cross-source synthesis for the orchestrator."""

    def __init__(
        self,
        settings: Settings,
        client,
        services: ServiceContainer | None,
        budget: BudgetTracker,
        trace_manager: TraceManager,
        db_factory_provider,
        invoker: AgentInvoker,
        events: EventPublisher,
        surfaces: SurfacePusher,
        plans: PlanStore,
        system_capability_handler,
        spawn_background,
    ):
        self._settings = settings
        self._client = client
        self._services = services
        self._budget = budget
        self._trace_manager = trace_manager
        # Provider (not a captured value) so reassigning db_factory on the
        # orchestrator propagates to this collaborator.
        self._db_factory_provider = db_factory_provider
        self._invoker = invoker
        self._events = events
        self._surfaces = surfaces
        self._plans = plans
        self._system_capability_handler = system_capability_handler
        self._spawn_background = spawn_background

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    def _request_services(self, db) -> ServiceContainer:
        """Return a ServiceContainer whose DB-bound services use ``db``."""
        from src.runtime import request_services

        return request_services(self._services, self._settings, db)

    async def run_cross_source_synthesis(
        self,
        source_names: list[str],
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Internal cross-source synthesis — no user-facing artifacts.

        Called by the scheduler when 2+ perception sources have new events
        in the same tick.  Asks the Planner to find cross-cutting insights
        and queues any resulting plans for background execution.

        Unlike process_message(), this does NOT create a lightweight run,
        Presenter formatting, or A2UI surface push.
        """
        trace = self._trace_manager.start_trace("cross_source_synthesis")
        try:
            planner_result = await self._invoker.call_agent(
                "planner",
                message=(
                    f"Synthesize recent observations across these sources: "
                    f"{', '.join(source_names)}. "
                    f"Identify cross-cutting insights, connections between "
                    f"events, or actions that span multiple sources."
                ),
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )
            # Queue any actionable plans from the synthesis
            plan = await self._queue_perception_plan(
                planner_result,
                "synthesis",
                user_id,
                workspace_id,
                trace.trace_id,
            )
            return {
                "status": "completed",
                "plan_goal": plan.goal if plan else None,
            }
        except Exception as e:
            logger.warning("Cross-source synthesis failed: %s", e, exc_info=True)
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def run_perception_cycle(self, source: str, user_id: str, workspace_id: str = "") -> dict:
        """Run a perception cycle for a specific data source.

        Step 1: Poll the connector directly (no Claude call — just API fetch).
        Step 2: If new events found, Librarian extracts entities/memories.
        Step 3: Planner evaluates importance and creates plans if needed.
        Step 4: Apply perception policy from planner response.
        Step 5: Extract decision and queue execution if actionable.
        """
        trace = self._trace_manager.start_trace(f"perception_{source}")

        try:
            # MCP-only integrations (e.g., Atlassian, Slack MCP server) have no
            # CONNECTOR_REGISTRY entry — their data flows entirely through
            # external MCP servers. Perception polling via native connectors
            # doesn't apply; short-circuit here so the scheduler doesn't log
            # perception_poll_failed warnings every tick for these sources.
            from src.connectors.base import CONNECTOR_REGISTRY

            if source not in CONNECTOR_REGISTRY:
                logger.debug(
                    "perception_skipped_mcp_only",
                    extra={"source": source},
                )
                return {"status": "skipped", "reason": "mcp_only_source", "source": source}

            # Check budget (only for Librarian + Planner calls, polling is cheap)
            async with self._db_factory() as db:
                budget_status = await self._budget.get_budget_status(db)
            if not self._budget.should_allow_perception(budget_status):
                logger.warning(
                    "perception_skipped_budget",
                    extra={"source": source, "mode": budget_status.budget_mode},
                )
                return {"status": "skipped", "reason": "budget_exhausted"}

            # Step 1: Poll the connector directly for new events
            raw_events, new_cursor, poll_error, cursor_type = await self._poll_connector(
                source, user_id, workspace_id
            )

            if poll_error:
                logger.warning(
                    "perception_poll_failed",
                    extra={"source": source, "error": poll_error},
                )
                return {"status": "error", "source": source, "error": poll_error}

            if not raw_events:
                logger.info(
                    "perception_no_new_events",
                    extra={"source": source},
                )
                # Save cursor even on empty polls so incremental sync
                # advances (e.g. Gmail historyId, Calendar syncToken).
                await self._update_cursor(source, user_id, workspace_id, new_cursor, cursor_type)
                return {"status": "completed", "source": source, "events": 0}

            # Ingest raw events into normalized_events table.
            # The cursor upsert is folded into the ingest session so that
            # "events ingested ⟹ cursor advanced" is a single gated commit —
            # the cursor only advances if the event loop ran to completion.
            event_summaries = await self._ingest_raw_events(
                raw_events,
                user_id,
                workspace_id,
                source=source,
                new_cursor=new_cursor,
                cursor_type=cursor_type,
            )

            # Fetch full thread context for reply emails
            thread_contexts = await _fetch_thread_contexts(raw_events, user_id, workspace_id)

            observer_summary = f"Polled {source}: {len(raw_events)} new event(s).\n" + "\n".join(
                f"- {s}" for s in event_summaries[:20]
            )
            if thread_contexts:
                observer_summary += "\n\n--- Thread Context (full conversation) ---"
                for tid, ctx in thread_contexts.items():
                    messages = ctx.get("messages", [])
                    if messages:
                        observer_summary += f"\nThread {tid} ({len(messages)} messages):"
                        for msg in messages[-5:]:
                            snippet = msg.get("snippet", msg.get("body", ""))[:200]
                            sender = msg.get("from", "unknown")
                            observer_summary += f"\n  [{sender}]: {snippet}"

            # Step 2: Librarian extracts entities and memories
            librarian_result = await self._invoker.call_agent(
                "librarian",
                message=f"Process these observations from {source} and extract "
                f"entities and memories:\n{observer_summary}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Enrich with correlation context for thread-aware planning
            correlation_context = ""
            if event_summaries:
                try:
                    from src.services.event_correlator import EventCorrelator

                    async with self._db_factory() as db:
                        correlator = EventCorrelator(db)
                        seen_entities: set[str] = set()
                        max_entities = 5
                        for raw_evt in raw_events:
                            if len(seen_entities) >= max_entities:
                                break
                            eid = getattr(raw_evt, "entity_id", None)
                            if eid and eid not in seen_entities:
                                seen_entities.add(eid)
                                thread = await correlator.detect_thread(
                                    user_id, eid, workspace_id=workspace_id
                                )
                                if thread and thread["event_count"] > 1:
                                    correlation_context += (
                                        f"\n[Thread detected] entity={thread['entity_id']} "
                                        f"has {thread['event_count']} events "
                                        f"(first: {thread['first_at']}, "
                                        f"last: {thread['last_at']})"
                                    )
                except Exception:
                    logger.warning("Correlation enrichment failed", exc_info=True)

            # Step 2b: Assess relevance of signals against user context
            try:
                from src.services.memory_service import MemoryService
                from src.services.relevance_assessor import (
                    PerceptionSignal,
                    UserContext,
                    assess_relevance,
                )

                signal = PerceptionSignal(
                    source=source,
                    event_type=f"perception_{source}",
                    summary=observer_summary[:500],
                )

                # Build user context from goals + preferences
                user_goals = []
                user_prefs = []
                try:
                    async with self._db_factory() as db:
                        mem_svc = MemoryService(self._settings, db)
                        # get_user_preferences(user_id, category, max_results, workspace_id)
                        prefs = await mem_svc.get_user_preferences(
                            user_id, workspace_id=workspace_id
                        )
                        for p in prefs[:10]:
                            if getattr(p, "memory_type", "") == "goal":
                                user_goals.append(p.fact_text)
                            else:
                                user_prefs.append(p.fact_text)
                except Exception:
                    logger.debug("Failed to load user context for relevance", exc_info=True)

                user_context = UserContext(
                    goals=user_goals,
                    preferences=user_prefs,
                )

                # Fetch engagement context + deterministic dismissal penalty.
                # is_suppressed() hard-stops 5+-dismissal signal types; the
                # graduated penalty (3-4 dismissals → 0.2) is applied to the
                # assessor score so borderline signals are demoted a tier.
                engagement_context = ""
                relevance_penalty = 0.0
                try:
                    from src.services.engagement_service import EngagementService

                    async with self._db_factory() as db:
                        eng_svc = EngagementService(db, workspace_id)
                        if await eng_svc.is_suppressed(signal.source, signal.event_type):
                            logger.debug(
                                "Signal suppressed: %s/%s",
                                signal.source,
                                signal.event_type,
                            )
                            return {"status": "suppressed", "source": source}
                        engagement_context = await eng_svc.get_engagement_context()
                        relevance_penalty = await eng_svc.get_relevance_penalty(
                            signal.source, signal.event_type
                        )
                except Exception:
                    logger.debug("Failed to load engagement context", exc_info=True)

                assessment = await assess_relevance(
                    signal,
                    user_context,
                    self._client,
                    engagement_context=engagement_context,
                    relevance_penalty=relevance_penalty,
                )

                # Route by notification tier
                if assessment.notification_tier == "briefing":
                    try:
                        async with self._db_factory() as db:
                            mem_svc = MemoryService(self._settings, db)
                            await mem_svc.store_briefing_memory(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                text=f"{observer_summary[:300]}\n\nWhy: {assessment.reasoning}",
                                source=f"perception:{source}",
                                relevance_score=assessment.relevance_score,
                                signal_source=source,
                            )
                            await db.commit()
                    except Exception:
                        logger.warning("Failed to store briefing memory", exc_info=True)

                elif assessment.notification_tier == "push":
                    try:
                        await self._surfaces.push_insight_surface(
                            signal, assessment, user_id, workspace_id
                        )
                    except Exception:
                        logger.warning(
                            "Failed to push insight surface for signal",
                            exc_info=True,
                        )

                else:
                    # silent tier: in world model from Librarian, record as ignored
                    try:
                        async with self._db_factory() as db:
                            from src.services.engagement_service import EngagementService

                            eng_svc = EngagementService(db, workspace_id)
                            await eng_svc.record_engagement(
                                signal.source, signal.event_type, "ignored"
                            )
                            await db.commit()
                    except Exception:
                        logger.debug("Failed to record silent tier engagement", exc_info=True)

            except Exception:
                logger.warning("Relevance assessment failed, continuing without", exc_info=True)

            # Step 3: Planner evaluates if any action is needed
            planner_message = (
                f"Evaluate these observations from {source}. "
                f"Create plans for anything important.\n"
                f"Optionally include a perception_policy JSON block to control "
                f"how soon {source} should next be checked:\n{observer_summary}"
            )
            if correlation_context:
                planner_message += f"\n\n--- Correlation Context ---{correlation_context}"

            planner_result = await self._invoker.call_agent(
                "planner",
                message=planner_message,
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Step 4: Extract and apply perception policy if present
            await self._apply_perception_policy_from_planner(
                planner_result, source, user_id, workspace_id, len(raw_events)
            )

            # Step 5: Extract plan and queue execution if actionable
            perception_plan = await self._queue_perception_plan(
                planner_result,
                source,
                user_id,
                workspace_id,
                trace.trace_id,
            )

            # Publish perception completed event
            await self._events.publish_event(
                "perception_completed",
                user_id,
                {
                    "source": source,
                    "trace_id": trace.trace_id,
                    "event_count": len(raw_events),
                    "plan_goal": perception_plan.goal if perception_plan else None,
                },
                workspace_id=workspace_id,
                trace_id=trace.trace_id,
            )

            return {
                "status": "completed",
                "source": source,
                "trace_id": trace.trace_id,
                "events": len(raw_events),
                "librarian": librarian_result,
                "planner": planner_result,
                "plan_goal": perception_plan.goal if perception_plan else None,
                "plan_id": perception_plan.plan_id if perception_plan else None,
            }

        except Exception as e:
            logger.error("perception_cycle failed: %s", e, exc_info=True)
            # DLQ: capture cycle-level failures for inspection/retry
            try:
                from src.services.dead_letter import DeadLetterService

                async with self._db_factory() as db:
                    dlq = DeadLetterService(db)
                    await dlq.enqueue(
                        user_id=user_id,
                        operation_type="perception_cycle",
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source_id=f"perception:{source}",
                        payload={
                            "source": source,
                            "trace_id": trace.trace_id,
                            "workspace_id": workspace_id,
                        },
                        workspace_id=workspace_id,
                    )
                    await db.commit()
            except Exception:
                logger.debug("DLQ enqueue failed for perception %s", source, exc_info=True)
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "source": source,
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _poll_connector(
        self, source: str, user_id: str, workspace_id: str
    ) -> tuple[list, str | None, str | None, str]:
        """Poll a connector for new events. Returns (events, new_cursor, error, cursor_type)."""
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.services.oauth_manager import OAuthManager

        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            return [], None, f"No connector registered for source: {source}", "opaque"

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
        access_token = await oauth_mgr.get_valid_token(user_id, oauth_provider)
        if not access_token:
            return (
                [],
                None,
                f"No valid credentials for {source} — user may need to re-authorize",
                cursor_type,
            )

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
            from src.connectors.poll_result import PollResult, error_class_to_policy_error

            result = await asyncio.wait_for(
                connector.poll(user_id, cursor, {"access_token": access_token}),
                timeout=30,
            )

            # Connectors now return PollResult; accept legacy 2-tuple for safety.
            if isinstance(result, PollResult):
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
            else:
                # Legacy 2-tuple fallback (non-native connectors)
                events, new_cursor = result
                return events, new_cursor, None, cursor_type

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
            logger.warning(
                "connector_poll_error",
                extra={"source": source, "error_code": error_code, "error": str(e)[:500]},
            )
            return [], None, f"Poll failed for {source} ({error_code}): {e}", cursor_type

    @staticmethod
    def _build_cursor_upsert_stmt(
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

    async def _ingest_raw_events(
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
                    event_id = await processor.process(
                        raw,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                    title = raw.title or getattr(raw, "raw_data", {}).get("subject", "")
                    summary = f"[{raw.source}] {raw.event_type}: {title}"
                    if event_id:
                        summary += f" (event_id={event_id})"
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
                stmt = self._build_cursor_upsert_stmt(
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

    async def _update_cursor(
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
        write directly into ``_ingest_raw_events`` instead.
        """
        if not new_cursor:
            return
        async with self._db_factory() as db:
            stmt = self._build_cursor_upsert_stmt(
                source, user_id, workspace_id, new_cursor, cursor_type
            )
            await db.execute(stmt)
            await db.commit()

    async def _apply_perception_policy_from_planner(
        self,
        planner_text: str,
        source: str,
        user_id: str,
        workspace_id: str,
        event_count: int,
    ) -> None:
        """Extract optional perception_policy from planner response and apply it.

        Falls back to deterministic defaults if the planner doesn't include
        a policy block or returns invalid JSON.
        """
        from src.contracts import PerceptionDecision
        from src.services.perception_policy import PerceptionPolicyService

        policy = self._extract_perception_policy(planner_text)
        if policy is None and event_count > 0:
            # Deterministic fallback: if events were found, check sooner
            policy = PerceptionDecision(
                next_check_seconds=120,
                urgency="normal",
                reasoning="events found, checking sooner",
            )

        if policy is None:
            return

        try:
            async with self._db_factory() as db:
                svc = PerceptionPolicyService(db)
                state = await svc.get_or_create_state(workspace_id, user_id, source)
                await svc.apply_agent_policy(
                    state,
                    next_check_seconds=policy.next_check_seconds,
                    watch_entities=policy.watch_entities if policy.watch_entities else None,
                )
                await db.commit()
        except Exception:
            logger.debug("Failed to apply perception policy", exc_info=True)

    async def _queue_perception_plan(
        self,
        planner_result: str,
        source: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> PlanOutput | None:
        """Extract a structured plan from the Planner's perception response
        and queue actionable plans for background execution.

        System capability steps are handled inline. Steps with write
        capabilities are persisted as Plan + background TaskRun.
        """
        import hashlib

        plan = extract_plan(planner_result)

        # Check if any steps are actionable
        has_system_caps = any(
            s.capability.startswith("system.") for s in plan.steps if s.actor == "jarvis"
        )
        has_write_steps = any(s.risk not in ("none",) for s in plan.steps if s.actor == "jarvis")
        has_tool_steps = any(
            not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
            for s in plan.steps
            if s.actor == "jarvis"
        )

        if not has_system_caps and not has_write_steps and not has_tool_steps:
            # No action to take, but the Planner may have produced a
            # cross-cutting insight (esp. on the synthesis path, which has no
            # prior relevance-routing step). Surface it as a briefing item so
            # the reasoning isn't silently discarded.
            if plan.goal and plan.goal.strip():
                try:
                    from src.services.memory_service import MemoryService

                    insight_text = plan.goal.strip()
                    if plan.reasoning and plan.reasoning.strip():
                        insight_text = f"{insight_text}\n\n{plan.reasoning.strip()}"
                    async with self._db_factory() as db:
                        mem_svc = MemoryService(self._settings, db)
                        await mem_svc.store_briefing_memory(
                            user_id=user_id,
                            workspace_id=workspace_id,
                            text=insight_text,
                            source=f"perception:{source}",
                            signal_source=source,
                        )
                        await db.commit()
                    logger.info(
                        "Perception insight from %s surfaced as briefing item",
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Failed to surface non-actionable perception insight from %s",
                        source,
                        exc_info=True,
                    )
            else:
                logger.debug(
                    "Perception plan from %s — no actionable steps, no insight",
                    source,
                )
            return plan

        # Handle system capability steps inline
        inline_caps = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        for step in plan.steps:
            if step.capability in inline_caps:
                try:
                    await self._system_capability_handler.handle_system_capability(
                        step, plan, user_id, workspace_id
                    )
                    logger.info(
                        "Perception inline handler: %s from %s",
                        step.capability,
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Perception inline handler failed: %s",
                        step.capability,
                        exc_info=True,
                    )

        # For steps requiring tool execution, persist and queue
        tool_steps = [
            s
            for s in plan.steps
            if s.actor == "jarvis"
            and not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
        ]
        if not tool_steps:
            return plan

        # Compute idempotency key
        goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
        idempotency_key = f"perception:{source}:{goal_hash}"

        # Persist Plan + PlanTasks
        plan = await self._plans.persist_plan_record(
            plan,
            user_id,
            workspace_id,
            trigger_type="perception",
            idempotency_key=idempotency_key,
        )

        if not plan.plan_id:
            logger.debug(
                "Plan not persisted (idempotent skip or error) for %s",
                source,
            )
            return plan

        # Create a background TaskRun for the scheduler
        try:
            async with self._db_factory() as db:
                from src.services.graph_executor import create_graph_executor

                executor = await create_graph_executor(
                    settings=self._settings,
                    db=db,
                    workspace_id=workspace_id,
                )
                run = await executor.create_run(
                    plan_id=plan.plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source="background",
                )
                await db.commit()

                logger.info(
                    "Perception queued plan %s → run %s from %s",
                    plan.plan_id,
                    run.run_id,
                    source,
                )
        except Exception:
            logger.warning(
                "Failed to create background run for perception plan %s",
                plan.plan_id,
                exc_info=True,
            )

        return plan

    async def _bump_perception_for_sources(
        self, sources: list[str], user_id: str, workspace_id: str
    ) -> None:
        """Signal immediate perception run for sources identified by intent classifier."""
        try:
            from src.services.perception_policy import PerceptionPolicyService

            async with self._db_factory() as db:
                svc = PerceptionPolicyService(db)
                for source in sources:
                    await svc.request_run(
                        workspace_id, user_id, source, signal_source="user_intent"
                    )
                await db.commit()
        except Exception:
            logger.warning("Failed to bump perception for sources", exc_info=True)

    @staticmethod
    def _extract_perception_policy(planner_text: str):
        """Parse a perception_policy JSON block from planner output, if present."""
        from src.contracts import PerceptionDecision

        if not planner_text or "perception_policy" not in planner_text:
            return None

        try:
            # Find the perception_policy JSON — could be embedded in markdown
            import re

            pattern = r'"perception_policy"\s*:\s*(\{[^}]+\})'
            match = re.search(pattern, planner_text)
            if not match:
                return None

            import json

            raw = json.loads(match.group(1))
            return PerceptionDecision(**raw)
        except Exception:
            logger.debug("Failed to parse perception_policy from planner", exc_info=True)
            return None
