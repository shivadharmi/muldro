"""Perception cycle dispatch driven by the perception_state table."""

import asyncio
import logging
from datetime import datetime, timezone

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

            # ----------------------------------------------------------------
            # Phase 1 — CLAIM (one short transaction; holds row locks briefly).
            # Select due rows FOR UPDATE SKIP LOCKED, throttle/sort, then LEASE
            # the ones we'll actually run (clear pending_run + advance next_run_at
            # by LEASE_TTL_S) and commit — releasing the locks BEFORE any cycle.
            # ----------------------------------------------------------------
            async with factory() as db:
                svc = PerceptionPolicyService(db)
                due_states = await svc.get_due_sources_all_users()

                if not due_states:
                    return

                # Drop (and pause) sources whose OAuth provider has no stored
                # token. A perception_state row can outlive its credential — the
                # user never finished connecting, or revoked/deleted the token —
                # and polling it just churns auth failures forever. Pausing stops
                # it being due; reconnecting OAuth + a wake signal reactivates it
                # via request_run. This also covers token deletion (the next tick
                # pauses any now-orphaned source), so no separate delete hook is
                # needed. The mutation commits with the claim transaction below.
                due_states = await self._drop_tokenless_sources(db, due_states)
                if not due_states:
                    await db.commit()  # persist any pauses
                    return

                # Rate limit: cap perception cycles per tick to avoid
                # API exhaustion when many sources are due simultaneously.
                # Applied BEFORE leasing so deferred sources keep their current
                # next_run_at and are re-picked next tick.
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

                # Lease only the sources we will run this tick. claim_due_sources
                # clears pending_run AND pushes next_run_at out by LEASE_TTL_S so
                # neither the pending flag nor the due-window can re-pick them
                # while the (unlocked) cycle runs.
                claimed = await svc.claim_due_sources(due_states)
                await db.commit()  # locks released here — cycles run unlocked

            logger.info("Perception tick: %d sources claimed", len(claimed))

            # ----------------------------------------------------------------
            # Phase 2 — RUN CYCLES (NO lock held). Each source's outcome is
            # recorded in its own fresh short transaction so one slow/failing
            # record never loses the others.
            # ----------------------------------------------------------------
            concurrency = getattr(self._settings, "perception_concurrency", None)
            if not isinstance(concurrency, int) or concurrency < 1:
                concurrency = 3
            perception_semaphore = asyncio.Semaphore(concurrency)

            async def _run_one(claim):
                async with perception_semaphore:
                    try:
                        ws_id = await self._resolve_workspace(claim.user_id)
                    except (ValueError, Exception):
                        ws_id = claim.workspace_id or ""

                    try:
                        result = await self._orchestrator.run_perception_cycle(
                            claim.source,
                            user_id=claim.user_id,
                            workspace_id=ws_id,
                        )
                        event_count = result.get("events", 0)
                        if result.get("status") == "error":
                            from src.connectors.poll_result import MISSING_ERROR_SENTINEL

                            await self._record_outcome(
                                factory,
                                claim,
                                error=result.get("error") or MISSING_ERROR_SENTINEL,
                            )
                        else:
                            await self._record_outcome(
                                factory,
                                claim,
                                event_count=event_count,
                                budget_multiplier=budget_multiplier,
                            )
                        return claim.source, event_count
                    except Exception as e:
                        # An uncategorized cycle failure has no recognized
                        # keyword, so a bare str(e) would classify as
                        # unknown (threshold 3). Fail-safe to the transient
                        # sentinel (threshold 6) — consistent with the
                        # connector-poller error paths. Keep the real error
                        # in the log for debuggability.
                        from src.connectors.poll_result import error_class_to_policy_error

                        await self._record_outcome(
                            factory,
                            claim,
                            error=error_class_to_policy_error("transient"),
                        )
                        logger.warning(
                            "Perception cycle failed for %s/%s: %s",
                            claim.user_id,
                            claim.source,
                            e,
                            extra={"error": str(e)[:512]},
                        )
                        return claim.source, 0

            results = await asyncio.gather(
                *(_run_one(c) for c in claimed),
                return_exceptions=True,
            )

            for i, r in enumerate(results):
                if isinstance(r, BaseException):
                    logger.warning(
                        "Perception gather exception for %s: %s",
                        claimed[i].source if i < len(claimed) else "unknown",
                        r,
                    )

            logger.info("Perception tick: %d sources processed", len(claimed))

            # ----------------------------------------------------------------
            # Phase 3 — CROSS-SOURCE SYNTHESIS — trigger on signal volume per
            # tenant. Group results by (user_id, workspace_id) so synthesis
            # never crosses tenant boundaries. Tenant identity comes from the
            # claimed snapshot; source name and event count come from the
            # result tuple. results is positionally aligned with claimed.
            # ----------------------------------------------------------------
            tenant_event_counts: dict[tuple[str, str], dict[str, int]] = {}
            for claim, r in zip(claimed, results):
                if isinstance(r, BaseException):
                    continue
                src_name, evt_count = r
                if evt_count > 0:
                    # Normalize workspace_id so None and "" map to the same key.
                    key = (claim.user_id, claim.workspace_id or "")
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
                                    "No workspace_id for cross-source synthesis user=%s, skipping",
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

    @staticmethod
    def _provider_for_source(source: str) -> str:
        """Map a perception source to its OAuth provider (gmail/calendar share
        the ``google`` provider). Mirrors connector_poller's mapping."""
        return "google" if source in ("gmail", "calendar") else source

    async def _drop_tokenless_sources(self, db, due_states):
        """Pause + drop due sources whose OAuth provider has no stored token.

        Checks token-row EXISTENCE (not validity) so a connected user mid
        token-refresh is never paused — only genuinely credential-less sources
        (never connected / revoked / deleted) are. Mutates ``mode`` on the
        already-locked rows; the caller commits."""
        from sqlalchemy import select

        from src.models.oauth_token import OAuthToken

        try:
            user_ids = {s.user_id for s in due_states}
            rows = await db.execute(
                select(OAuthToken.user_id, OAuthToken.provider).where(
                    OAuthToken.user_id.in_(user_ids)
                )
            )
            have = {(r[0], r[1]) for r in rows.all()}
        except Exception:
            # Fail-open: only PAUSE on a positive "no token" confirmation. If the
            # lookup itself fails, keep every source rather than risk pausing a
            # legitimate one on a transient DB hiccup.
            logger.debug("Token-presence check failed; keeping all due sources", exc_info=True)
            return due_states

        keep = []
        for s in due_states:
            if (s.user_id, self._provider_for_source(s.source)) in have:
                keep.append(s)
            else:
                s.mode = "paused"
                logger.info(
                    "Pausing perception source %s/%s — no OAuth token for provider %s",
                    s.user_id,
                    s.source,
                    self._provider_for_source(s.source),
                )
        return keep

    async def _record_outcome(
        self,
        factory,
        claim,
        *,
        event_count: int = 0,
        budget_multiplier: int = 1,
        error: str | None = None,
    ) -> None:
        """Record one source's cycle outcome in a FRESH short transaction.

        Re-fetches the leased PerceptionState by id and calls record_success /
        record_failure exactly as before (same signatures/semantics). Each
        source records independently — one slow/failing record never loses the
        others. If the row vanished (e.g. deleted mid-cycle) the outcome is
        simply dropped; the lease has already advanced next_run_at.
        """
        from src.services.perception_policy import PerceptionPolicyService

        async with factory() as rec_db:
            svc = PerceptionPolicyService(rec_db)
            state = await svc.get_by_state_id(claim.state_id)
            if state is None:
                logger.warning(
                    "Perception state %s vanished before outcome recording", claim.state_id
                )
                return
            # Detect a wakeup signal (webhook/intent/agent → request_run) that
            # landed DURING the cycle. The claim transaction committed
            # pending_run=False, so a re-fetched pending_run=True means a fresh
            # signal arrived while the (now-unlocked) cycle was running.
            signalled_mid_cycle = bool(state.pending_run)

            if error is not None:
                await svc.record_failure(state, error)
            else:
                await svc.record_success(state, event_count, budget_multiplier=budget_multiplier)

            # record_success/record_failure just cleared pending_run and pushed
            # next_run_at out, which would swallow a mid-cycle signal (the old
            # held-lock design serialized the signal after recording so it
            # survived). Re-arm so the next tick perceives the signalled source.
            if signalled_mid_cycle:
                state.pending_run = True
                state.next_run_at = datetime.now(timezone.utc)
            await rec_db.commit()
