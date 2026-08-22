"""Schedule action dispatch (``_fire``) for every action_type."""

import logging

from sqlalchemy import select

from src.deep_runtime.authorization import AuthorizationSource
from src.models.database import get_session_factory
from src.models.schedules import Schedule
from src.services.heartbeat import HeartbeatService

logger = logging.getLogger(__name__)


class ScheduleDispatchMixin:
    """Dispatches a due schedule's action via the orchestrator."""

    async def _fire(self, sched: Schedule) -> None:
        """Dispatch a single schedule's action via the orchestrator."""
        config = sched.action_config or {}
        action = sched.action_type

        # Resolve workspace_id for workspace-scoped calls
        try:
            workspace_id = await self._resolve_workspace(sched.user_id)
        except ValueError:
            workspace_id = ""

        if action == "observe_source":
            source = config["source"]
            if not self._orchestrator:
                raise RuntimeError("Orchestrator required for observe_source")

            # Check if perception_state manages this source — if so, skip
            # (perception_state is the primary mechanism; schedules are fallback)
            try:
                from src.models.perception_state import PerceptionState

                factory = get_session_factory()
                async with factory() as db:
                    result = await db.execute(
                        select(PerceptionState).where(
                            PerceptionState.user_id == sched.user_id,
                            PerceptionState.source == source,
                            PerceptionState.mode != "paused",
                        )
                    )
                    if result.scalar_one_or_none() is not None:
                        logger.debug(
                            "observe_source skipped for %s — managed by perception_state",
                            source,
                        )
                        return
            except Exception:
                pass  # fall through to legacy path

            # Legacy fallback: gate on auth and run directly
            factory = get_session_factory()
            async with factory() as db:
                authorized = await self._get_authorized_providers(db, sched.user_id)
            if source not in authorized:
                logger.info(
                    "Skipping observe_source for %s — no active connector",
                    source,
                )
                return

            await self._orchestrator.run_perception_cycle(
                source, user_id=sched.user_id, workspace_id=workspace_id
            )
        elif action == "generate_briefing":
            if self._orchestrator:
                await self._orchestrator.generate_briefing(
                    user_id=sched.user_id, workspace_id=workspace_id
                )
            else:
                raise RuntimeError("Orchestrator required for generate_briefing")
        elif action == "meeting_prep":
            if self._orchestrator:
                await self._orchestrator.process_message(
                    message="Check calendar for meetings in next 30min. "
                    "If found, generate meeting prep and deliver to user.",
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                    authorization_source=AuthorizationSource.AUTONOMOUS,
                )
            else:
                raise RuntimeError("Orchestrator required for meeting_prep")
        elif action == "heartbeat":
            factory = get_session_factory()
            async with factory() as hb_db:
                hb = HeartbeatService(self._settings, hb_db, notifier=self._resolve_notifier(hb_db))
                await hb.run(sched.user_id)
                await hb_db.commit()
        elif action == "check_slos":
            from src.services.alerting import AlertingService
            from src.services.trace_store import TraceStore

            trace_store = TraceStore()
            alerting = AlertingService(trace_store=trace_store)
            checks = await alerting.check_all_slos()
            logger.info(
                "SLO check complete: %s",
                ", ".join(f"{c.name}={c.status}" for c in checks),
            )
        elif action == "consolidate_memories":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.memory_service import MemoryService

                ms = MemoryService(settings=self._settings, db=db)
                merged = await ms.consolidate_memories(user_id, workspace_id=workspace_id)
                await db.commit()
                logger.info("Memory consolidation for %s: %d merged", user_id, merged)
        elif action == "evaluate_time_triggers":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.watcher_service import WatcherService

                ws = WatcherService(db=db)
                insights = await ws._evaluate_time_triggers(user_id)
                await db.commit()
                if insights:
                    logger.info("Time triggers for %s: %d fired", user_id, len(insights))
        elif action == "run_watchers":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.watcher_service import WatcherService

                ws = WatcherService(db=db)
                insights = await ws.run_all_watchers(user_id)
                await db.commit()
                if insights:
                    logger.info("Watchers for %s: %d insights", user_id, len(insights))
        elif action == "custom_agent_task":
            instructions = config.get("instructions", "")
            if self._orchestrator:
                # The founder authorized these INSTRUCTIONS when creating the schedule,
                # not each write they turn out to imply. `mode="execute"` now only means
                # the plan is not marked `requires_user_input`; it no longer decides
                # whether a risky step runs. Every write is gated at action time, and
                # because provenance is AUTONOMOUS and nobody is present, a gated write is
                # PREPARED into the review queue rather than executed or interrupted on.
                await self._orchestrator.process_message(
                    message=instructions,
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                    mode="execute",
                    authorization_source=AuthorizationSource.AUTONOMOUS,
                )
            else:
                raise RuntimeError("Orchestrator required for custom_agent_task")
        elif action == "wake_agent":
            # Agent-requested wakeup — bridge between agent decisions and perception
            agent = config.get("agent", "perceiver")
            source = config.get("source")
            if agent == "perceiver" and source:
                from src.services.perception_policy import PerceptionPolicyService

                factory = get_session_factory()
                async with factory() as db:
                    svc = PerceptionPolicyService(db)
                    await svc.request_run(
                        workspace_id=workspace_id,
                        user_id=sched.user_id,
                        source=source,
                        signal_source="agent",
                    )
                    await db.commit()
            elif self._orchestrator:
                msg = config.get("message", f"Wake {agent}")
                await self._orchestrator.process_message(
                    message=msg,
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                    authorization_source=AuthorizationSource.AUTONOMOUS,
                )
        else:
            logger.warning("Unknown action_type: %s for schedule %s", action, sched.schedule_id)
