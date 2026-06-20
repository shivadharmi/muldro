"""Ambient perception cycle coordinator.

Thin orchestration layer over PerceptionPolicyService.  The policy service
owns all scheduling math (intervals, circuit breaker, starvation, budget);
this coordinator wires it to the orchestrator's run_perception_cycle() and
domain events.

Each cycle: Observer -> Librarian -> Planner -> (Governor -> Presenter if needed).
"""

import logging

logger = logging.getLogger(__name__)


class PerceptionCoordinator:
    """Coordinates ambient perception cycles for a single user.

    Delegates due-source resolution and lifecycle tracking to the
    DB-backed ``PerceptionPolicyService``.
    """

    def __init__(self, orchestrator, user_id: str, workspace_id: str = ""):
        self._orchestrator = orchestrator
        self._user_id = user_id
        self._workspace_id = workspace_id

    # ------------------------------------------------------------------
    # Due-source resolution
    # ------------------------------------------------------------------

    async def get_due_sources(self, budget_multiplier: int = 1):
        """Return PerceptionState rows that are due for observation."""
        from src.models.database import get_session_factory
        from src.services.perception_policy import PerceptionPolicyService

        factory = get_session_factory()
        async with factory() as db:
            svc = PerceptionPolicyService(db)
            return await svc.get_due_sources(self._user_id, budget_multiplier)

    # ------------------------------------------------------------------
    # Cycle execution
    # ------------------------------------------------------------------

    async def run_due_cycles(self, budget_multiplier: int = 1) -> list[dict]:
        """Run perception cycles for all due sources.

        Replaces the old in-memory interval tracking with DB-backed policy.
        """
        from src.models.database import get_session_factory
        from src.services.perception_policy import PerceptionPolicyService

        factory = get_session_factory()
        async with factory() as db:
            svc = PerceptionPolicyService(db)
            due_states = await svc.get_due_sources(self._user_id, budget_multiplier)

            results: list[dict] = []
            for state in due_states:
                source = state.source
                logger.info("perception_cycle_starting", extra={"source": source})

                try:
                    result = await self._orchestrator.run_perception_cycle(
                        source,
                        user_id=self._user_id,
                        workspace_id=self._workspace_id,
                    )
                    results.append(result)

                    if result.get("status") == "error":
                        from src.connectors.poll_result import MISSING_ERROR_SENTINEL

                        await svc.record_failure(
                            state, result.get("error") or MISSING_ERROR_SENTINEL
                        )
                    else:
                        event_count = result.get("events", 0)
                        await svc.record_success(state, event_count)

                    await self._publish_event(
                        "connector.synced",
                        self._user_id,
                        {"source": source, "status": result.get("status", "ok")},
                    )
                except Exception as e:
                    # An uncategorized cycle failure has no recognized keyword,
                    # so a bare str(e) would classify as unknown (threshold 3).
                    # Fail-safe to the transient sentinel (threshold 6) —
                    # consistent with the connector-poller error paths. The
                    # real error is preserved in the log extra below.
                    from src.connectors.poll_result import error_class_to_policy_error

                    logger.error(
                        "perception_cycle_failed",
                        extra={"source": source, "error": str(e)},
                    )
                    results.append({"status": "error", "source": source, "error": str(e)})
                    await svc.record_failure(state, error_class_to_policy_error("transient"))
                    await self._publish_event(
                        "connector.error",
                        self._user_id,
                        {"source": source, "error": str(e)[:500]},
                    )

            await db.commit()
        return results

    # ------------------------------------------------------------------
    # Push source sync
    # ------------------------------------------------------------------

    async def sync_push_sources(self) -> None:
        """Update PerceptionState mode based on active webhook subscriptions."""
        try:
            from src.integrations.sync.webhook_manager import WebhookManager
            from src.models.database import get_session_factory
            from src.services.perception_policy import PerceptionPolicyService

            factory = get_session_factory()
            async with factory() as db:
                mgr = WebhookManager(db, self._workspace_id, callback_base_url="")
                push_sources = await mgr.get_sources_with_push()

                svc = PerceptionPolicyService(db)
                for source in push_sources:
                    state = await svc.get_or_create_state(self._workspace_id, self._user_id, source)
                    if state.mode == "poll":
                        state.mode = "push"
                        await db.flush()
                await db.commit()

                logger.info(
                    "push_sources_synced",
                    extra={"sources": list(push_sources)},
                )
        except Exception as e:
            logger.debug("Failed to sync push sources: %s", e)

    # ------------------------------------------------------------------
    # Auth refresh
    # ------------------------------------------------------------------

    async def refresh_enabled_sources(self) -> None:
        """Pause PerceptionState for sources that lost authorization."""
        try:
            from src.models.database import get_session_factory
            from src.services.perception_policy import PerceptionPolicyService
            from src.services.scheduler import SchedulerLoop

            factory = get_session_factory()
            async with factory() as db:
                authorized = await SchedulerLoop._get_authorized_providers(db, self._user_id)
                svc = PerceptionPolicyService(db)
                states = await svc.get_due_sources(self._user_id)

                for state in states:
                    if state.source not in authorized and state.mode != "paused":
                        state.mode = "paused"
                        logger.info(
                            "perception_pausing_unauthorized",
                            extra={"source": state.source},
                        )
                await db.commit()
        except Exception:
            logger.debug("Failed to refresh authorized sources", exc_info=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _publish_event(self, event_type: str, user_id: str, payload: dict) -> None:
        """Publish a domain event via the orchestrator's event bus (best-effort)."""
        try:
            await self._orchestrator._publish_event(event_type, user_id, payload)
        except Exception:
            logger.debug("Failed to publish %s event", event_type, exc_info=True)
