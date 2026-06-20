"""Perception cycle dispatch driven by the perception_state table."""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class PerceptionTickMixin:
    """Runs perception cycles for due sources + cross-source synthesis."""

    async def _tick_perception(self, factory) -> None:
        """Run perception cycles for sources with pending_run or next_run_at <= now."""
        if not self._orchestrator:
            return

        try:
            from src.services.perception_policy import PerceptionPolicyService

            budget_status = None
            budget_multiplier = 1
            try:
                async with factory() as db:
                    budget_status = await self._orchestrator._budget.get_budget_status(db)
                    if not self._orchestrator._budget.should_allow_perception(budget_status):
                        return
                    budget_multiplier = (
                        self._orchestrator._budget.get_perception_interval_multiplier(budget_status)
                    )
            except Exception:
                logger.debug("Budget check failed, proceeding with defaults", exc_info=True)

            async with factory() as db:
                svc = PerceptionPolicyService(db)
                due_states = await svc.get_due_sources_all_users()

                if not due_states:
                    return

                # Rate limit: cap perception cycles per tick to avoid
                # API exhaustion when many sources are due simultaneously
                max_per_tick = self._settings.max_perception_per_tick
                if len(due_states) > max_per_tick:
                    # Priority: pending_run=True first (explicit user/agent requests),
                    # then by next_run_at ascending (oldest first)
                    due_states.sort(
                        key=lambda s: (not s.pending_run, s.next_run_at or datetime.max)
                    )
                    logger.info(
                        "Perception throttled: %d due, processing %d, deferring %d",
                        len(due_states),
                        max_per_tick,
                        len(due_states) - max_per_tick,
                    )
                    due_states = due_states[:max_per_tick]

                # C4: Clear pending_run BEFORE running cycles to prevent
                # the next 30s tick from double-picking the same sources
                for state in due_states:
                    state.pending_run = False
                await db.flush()

                concurrency = getattr(self._settings, "perception_concurrency", None)
                if not isinstance(concurrency, int) or concurrency < 1:
                    concurrency = 3
                perception_semaphore = asyncio.Semaphore(concurrency)

                async def _run_one(state):
                    async with perception_semaphore:
                        try:
                            ws_id = await self._resolve_workspace(state.user_id)
                        except (ValueError, Exception):
                            ws_id = state.workspace_id or ""

                        try:
                            result = await self._orchestrator.run_perception_cycle(
                                state.source,
                                user_id=state.user_id,
                                workspace_id=ws_id,
                            )
                            event_count = result.get("events", 0)
                            if result.get("status") == "error":
                                from src.connectors.poll_result import MISSING_ERROR_SENTINEL

                                await svc.record_failure(
                                    state, result.get("error") or MISSING_ERROR_SENTINEL
                                )
                            else:
                                await svc.record_success(
                                    state, event_count, budget_multiplier=budget_multiplier
                                )
                            return state.source, event_count
                        except Exception as e:
                            # An uncategorized cycle failure has no recognized
                            # keyword, so a bare str(e) would classify as
                            # unknown (threshold 3). Fail-safe to the transient
                            # sentinel (threshold 6) — consistent with the
                            # connector-poller error paths. Keep the real error
                            # in the log for debuggability.
                            from src.connectors.poll_result import error_class_to_policy_error

                            await svc.record_failure(
                                state, error_class_to_policy_error("transient")
                            )
                            logger.warning(
                                "Perception cycle failed for %s/%s: %s",
                                state.user_id,
                                state.source,
                                e,
                                extra={"error": str(e)[:512]},
                            )
                            return state.source, 0

                results = await asyncio.gather(
                    *(_run_one(s) for s in due_states),
                    return_exceptions=True,
                )

                for i, r in enumerate(results):
                    if isinstance(r, BaseException):
                        logger.warning(
                            "Perception gather exception for %s: %s",
                            due_states[i].source if i < len(due_states) else "unknown",
                            r,
                        )

                await db.commit()
                logger.info("Perception tick: %d sources processed", len(due_states))

                # D2: Cross-source synthesis — trigger on signal volume per tenant.
                # Group results by (user_id, workspace_id) so synthesis never
                # crosses tenant boundaries.
                # Tenant identity (user_id, workspace_id) comes from due_state;
                # polled source name and event count come from the result tuple.
                # results is positionally aligned with due_states.
                tenant_event_counts: dict[tuple[str, str], dict[str, int]] = {}
                for state, r in zip(due_states, results):
                    if isinstance(r, BaseException):
                        continue
                    src_name, evt_count = r
                    if evt_count > 0:
                        # Normalize workspace_id so None and "" map to the same key.
                        key = (state.user_id, state.workspace_id or "")
                        tenant_event_counts.setdefault(key, {})[src_name] = evt_count

                for key, src_counts in tenant_event_counts.items():
                    tenant_user_id, ws_raw = key
                    sources_with_events = len(src_counts)
                    total_event_count = sum(src_counts.values())
                    if sources_with_events >= 2 and total_event_count >= 3 and self._orchestrator:
                        try:
                            ws_id = ws_raw
                            if not ws_id:
                                try:
                                    ws_id = await self._resolve_workspace(tenant_user_id)
                                except Exception:
                                    logger.warning(
                                        "No workspace_id for cross-source synthesis user=%s, "
                                        "skipping",
                                        tenant_user_id,
                                    )
                                    ws_id = ""
                            if ws_id:
                                await self._orchestrator.run_cross_source_synthesis(
                                    source_names=list(src_counts.keys()),
                                    user_id=tenant_user_id,
                                    workspace_id=ws_id,
                                )
                                logger.info(
                                    "Cross-source synthesis triggered for user=%s %d sources",
                                    tenant_user_id,
                                    sources_with_events,
                                )
                        except Exception:
                            logger.warning(
                                "Cross-source synthesis failed for user=%s",
                                tenant_user_id,
                                exc_info=True,
                            )
        except Exception:
            logger.warning("Perception tick error", exc_info=True)
