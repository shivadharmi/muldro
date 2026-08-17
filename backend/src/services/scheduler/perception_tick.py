"""Perception cycle dispatch driven by the perception_state table."""

import asyncio
import logging
from datetime import datetime, timezone

from src.integrations.gateway_actions import gateway_provider_for_source

logger = logging.getLogger(__name__)

# Token-acquisition reasons that mean the credential is PERMANENTLY unusable and
# the user must re-authorize (never connected / no refresh token / revoked). A
# ``refresh_failed`` reason is transient (a network/5xx blip on the provider's
# token endpoint) and is NOT in this set — those sources stay runnable so the
# normal poll/circuit-breaker flow handles a genuine outage.
_PERMANENT_REAUTH_REASONS = frozenset({"no_token", "no_refresh_token", "revoked"})


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
            # Re-auth tuples marked during the gate. Collected inside the claim
            # transaction (writes apply on the locked `db`), then notified AFTER
            # the commit below — notify is Redis + external delivery and must be
            # post-commit so a rollback never leaves a stale reconnect prompt.
            reauth_marked: list[tuple[str, str, str, str]] = []

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
                # needed. The pause writes apply on THIS locked `db` (one
                # transaction, no self-deadlock) and commit below.
                due_states = await self._drop_tokenless_sources(
                    db, due_states, marked_out=reauth_marked
                )
                if not due_states:
                    await db.commit()  # persist any pauses
                    await self._notify_reauth_marked(reauth_marked)
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

            # Notify post-commit: the needs-reauth pauses are now durable.
            await self._notify_reauth_marked(reauth_marked)

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
        the ``google`` provider).

        Delegates to :mod:`src.integrations.provider_map` (the canonical map).
        """
        from src.integrations.provider_map import provider_for_source

        return provider_for_source(source)

    def _validity_gate_collaborators(self):
        """Resolve the (OAuthManager, ReauthService) used by the validity gate.

        Reuses the orchestrator's existing service container (the same
        ``OAuthManager`` and ``Notifier`` the app wires) when reachable, and
        builds a ``ReauthService`` around the shared notifier + Redis so its
        reconnect prompts are deduped. Degrades gracefully: if no OAuthManager
        is available, returns ``(None, None)`` and the gate keeps every source
        (fail-open). If a notifier/redis is missing, the ReauthService is still
        built — ``mark_needs_reauth`` will pause sources and (with no Redis) skip
        dedup, so the reconnect prompt is best-effort rather than silent.
        """
        services = getattr(self._orchestrator, "_services", None)

        oauth_manager = getattr(services, "oauth_manager", None) if services else None
        if oauth_manager is None:
            # Fall back to constructing one the same way app.py / routes do.
            try:
                from src.models.database import get_session_factory
                from src.services.oauth_manager import OAuthManager

                oauth_manager = OAuthManager(
                    db_factory=get_session_factory(),
                    settings=self._settings,
                    encryption_key=getattr(self._settings, "oauth_encryption_key", ""),
                )
            except Exception:
                logger.debug("Could not build OAuthManager for validity gate", exc_info=True)
                return None, None

        notifier = getattr(services, "notifier", None) if services else None
        redis = None
        if services is not None:
            extras = getattr(services, "extras", None)
            if isinstance(extras, dict):
                redis = extras.get("redis")

        try:
            from src.models.database import get_session_factory
            from src.services.reauth_service import ReauthService

            reauth_service = ReauthService(
                db_factory=get_session_factory(),
                notifier=notifier,
                redis=redis,
                settings=self._settings,
            )
        except Exception:
            logger.debug("Could not build ReauthService for validity gate", exc_info=True)
            return oauth_manager, None

        return oauth_manager, reauth_service

    async def _gateway_connection_index(
        self, db, due_states
    ) -> dict[tuple[str, str], set[str] | None]:
        """Batch-resolve which OC providers each principal can actually resolve.

        Returns ``{(workspace_id, user_id): active_provider_ids | None}``, where
        ``None`` means "could not determine" and is treated fail-open by the
        caller (same posture as the OAuth branch's validity-check failure).

        ONE query per distinct ``(workspace_id, user_id)`` — never one per source
        — mirroring the per-``(user, provider)`` caching the OAuth branch uses, so
        a tick with many due gateway sources still costs a handful of selects.

        The lookup delegates to ``integration_status.active_connection_providers``
        rather than restating its WHERE clause: "runnable" must mean exactly what
        ``adapter/connection_resolver`` will resolve (workspace + principal +
        provider + the DEFAULT account alias + ``connection_status == "active"``),
        and a second copy of that query is how the two definitions drift apart.
        """
        from src.services import integration_status

        wanted: dict[tuple[str, str], set[str]] = {}
        for s in due_states:
            provider = gateway_provider_for_source(s.source)
            if provider is None:
                continue
            wanted.setdefault((s.workspace_id or "", s.user_id), set()).add(provider)

        index: dict[tuple[str, str], set[str] | None] = {}
        for key, providers in wanted.items():
            workspace_id, user_id = key
            if not workspace_id:
                # A source with no workspace cannot be scoped to a connection.
                # Fail-open (keep) rather than silently going dark: the cycle's
                # own error path reports a real failure, which is visible.
                logger.debug("Gateway source for %s has no workspace_id; keeping", user_id)
                index[key] = None
                continue
            try:
                index[key] = await integration_status.active_connection_providers(
                    db, workspace_id, user_id, tuple(sorted(providers))
                )
            except Exception:
                logger.debug(
                    "Gateway connection lookup failed for %s/%s; keeping sources",
                    workspace_id,
                    user_id,
                    exc_info=True,
                )
                index[key] = None
        return index

    async def _drop_tokenless_sources(self, db, due_states, marked_out=None):
        """Runnability gate: drop sources whose credential cannot be used, keep
        the rest. Two branches, because Jarvis now holds credentials two ways.

        **Gateway-backed sources** (``gateway_provider_for_source`` resolves —
        gmail, calendar) hold their credential inside OpenConnector, recorded
        locally as a ``connection_map`` row. OAuthManager holds no token for them
        and no route can mint one, so consulting it answers ``no_token`` forever
        — a PERMANENT reason, which used to drop these sources AND mark them
        ``needs_reauth`` unrecoverably, silently ending Gmail/Calendar
        perception. These sources are decided ONLY by an active connection and
        never touch OAuthManager. When there is no active connection the source
        is SKIPPED, deliberately NOT marked ``needs_reauth``: "not connected yet"
        is not a revoked credential, and the reauth mark pauses the row behind a
        recovery path (``_tick_reauth_recovery``) that can only ever re-check
        OAuthManager — i.e. it would strand the source exactly as the bug did.
        Skipping leaves the row due, so the next tick re-checks it and it starts
        running the moment the user finishes connecting.

        **OAuth-backed sources** (slack, notion, ... — everything else) keep the
        original behaviour verbatim:

        For each due source, group by ``(user_id, provider)`` and call
        ``OAuthManager.get_valid_token_with_reason`` ONCE per pair (cached within
        the tick). Then:

        - ``reason == "ok"``                    → KEEP the source (runnable).
        - reason in the PERMANENT set           → DROP the source and apply the
          needs-reauth DB writes ON THE PASSED-IN ``db`` via
          ``ReauthService.apply_needs_reauth`` (one set of writes per (user,
          provider)). Doing this on the caller's session — the one already
          holding ``FOR UPDATE`` locks on these ``perception_state`` rows —
          keeps everything in ONE transaction and avoids a cross-session
          self-deadlock that ``mark_needs_reauth`` (which opens a second
          session) would cause.
        - ``reason == "refresh_failed"``        → KEEP the source (transient blip;
          let the poll/circuit-breaker flow handle a genuine outage).

        The reconnect NOTIFICATION is Redis + external delivery and must run
        post-commit, so it is NOT sent here. Each marked ``(user_id, provider,
        reason, workspace_id)`` tuple is appended to ``marked_out`` (when given)
        so the caller can notify after committing.

        Fail-open: if the OAuthManager is unavailable or the validity check itself
        throws, every source is kept rather than nuking the tick.
        """
        gateway_index = await self._gateway_connection_index(db, due_states)

        # Only build the OAuth collaborators when a non-gateway source is due —
        # a gmail/calendar-only tick must not need an OAuthManager to exist.
        oauth_manager = reauth_service = None
        if any(gateway_provider_for_source(s.source) is None for s in due_states):
            oauth_manager, reauth_service = self._validity_gate_collaborators()

        # Cache one validity result per (user, provider) for the tick.
        reason_cache: dict[tuple[str, str], str] = {}
        # Providers already surfaced for re-auth this tick (one set of writes
        # per pair).
        marked: set[tuple[str, str]] = set()
        # (user, provider) pairs already logged as unconnected this tick.
        unconnected_logged: set[tuple[str, str]] = set()

        keep = []
        for s in due_states:
            gateway_provider = gateway_provider_for_source(s.source)
            if gateway_provider is not None:
                active = gateway_index.get((s.workspace_id or "", s.user_id))
                if active is None or gateway_provider in active:
                    keep.append(s)
                    continue
                key = (s.user_id, gateway_provider)
                if key not in unconnected_logged:
                    unconnected_logged.add(key)
                    logger.info(
                        "Skipping perception source %s/%s — gateway provider %s "
                        "has no active connection (not paused; runs on connect)",
                        s.user_id,
                        s.source,
                        gateway_provider,
                    )
                continue

            if oauth_manager is None:
                # No OAuthManager available — fail-open for the OAuth branch.
                keep.append(s)
                continue

            provider = self._provider_for_source(s.source)
            key = (s.user_id, provider)
            try:
                reason = reason_cache.get(key)
                if reason is None:
                    result = await oauth_manager.get_valid_token_with_reason(s.user_id, provider)
                    reason = result.reason
                    reason_cache[key] = reason
            except Exception:
                # Fail-open: an unexpected validity-check error keeps the source
                # rather than risk pausing a legitimate one on a transient hiccup.
                logger.debug(
                    "OAuth validity check failed for %s/%s; keeping source",
                    s.user_id,
                    provider,
                    exc_info=True,
                )
                keep.append(s)
                continue

            if reason in _PERMANENT_REAUTH_REASONS:
                # Drop from the runnable list and apply the needs-reauth writes
                # once per pair on the caller's locked session (no commit/notify
                # here — the tick commits, then notifies post-commit).
                if reauth_service is not None and key not in marked:
                    marked.add(key)
                    try:
                        await reauth_service.apply_needs_reauth(
                            db,
                            s.user_id,
                            provider,
                            reason,
                        )
                        if marked_out is not None:
                            marked_out.append((s.user_id, provider, reason, s.workspace_id or ""))
                    except Exception:
                        logger.warning(
                            "Failed to apply needs_reauth for %s/%s",
                            s.user_id,
                            provider,
                            exc_info=True,
                        )
                logger.info(
                    "Dropping perception source %s/%s — provider %s needs re-auth (%s)",
                    s.user_id,
                    s.source,
                    provider,
                    reason,
                )
            else:
                # "ok" or "refresh_failed" (transient) → keep runnable.
                keep.append(s)
        return keep

    async def _notify_reauth_marked(self, marked) -> None:
        """Send post-commit reconnect prompts for gate-marked providers.

        ``marked`` is a list of ``(user_id, provider, reason, workspace_id)``
        tuples collected by ``_drop_tokenless_sources``. Notification is Redis +
        external delivery (deduped inside ``notify_reauth``) and must run AFTER
        the tick's commit so a rollback never leaves a stale prompt. Fail-open:
        a notify failure for one provider never blocks the others or the tick.
        """
        if not marked:
            return
        _oauth, reauth_service = self._validity_gate_collaborators()
        if reauth_service is None:
            return
        for user_id, provider, reason, workspace_id in marked:
            try:
                await reauth_service.notify_reauth(
                    user_id, provider, reason, workspace_id=workspace_id
                )
            except Exception:
                logger.warning(
                    "Failed to notify needs_reauth for %s/%s",
                    user_id,
                    provider,
                    exc_info=True,
                )

    async def _tick_reauth_recovery(self, factory) -> None:
        """Self-healing backstop: resume providers stuck needing re-auth once valid.

        The OAuth callback normally calls ``clear_reauth`` on reconnect. This
        reaper covers the case where that callback was missed (e.g. token
        refreshed out-of-band). It scans TWO independent backstops, sharing one
        validity-check + clear cache per (user, provider) so neither double-acts:

        1. **Perception-source branch** — ``perception_state`` rows paused with
           ``last_error == "needs_reauth"``. Covers providers WITH a perception
           source (gmail/calendar/slack/github).
        2. **TaskRun branch** — ``TaskRun`` rows in ``awaiting_reauth``, grouped
           by ``checkpoint["awaiting_provider"]``. Covers providers with NO
           perception source (notion/atlassian): a deferred autonomous run on
           those providers has no perception-source backstop, so without this it
           would never recover if the callback was missed.

        On ``reason == "ok"`` it calls ``clear_reauth`` (which resumes any
        sources AND requeues deferred runs). Fully fail-open: any error leaves
        the stuck state untouched.
        """
        try:
            oauth_manager, reauth_service = self._validity_gate_collaborators()
            if oauth_manager is None or reauth_service is None:
                return

            from sqlalchemy import select

            from src.models.perception_state import PerceptionState
            from src.models.task_graph import TaskRun

            async with factory() as db:
                pstate_result = await db.execute(
                    select(PerceptionState).where(
                        PerceptionState.mode == "paused",
                        PerceptionState.last_error == "needs_reauth",
                    )
                )
                paused = list(pstate_result.scalars().all())

                run_result = await db.execute(
                    select(TaskRun).where(TaskRun.status == "awaiting_reauth")
                )
                awaiting_runs = list(run_result.scalars().all())

            if not paused and not awaiting_runs:
                return

            # Shared across both branches: one validity check + at most one
            # clear per (user, provider).
            checked: dict[tuple[str, str], str] = {}
            cleared: set[tuple[str, str]] = set()

            # Branch 1: paused perception sources.
            for s in paused:
                provider = self._provider_for_source(s.source)
                await self._maybe_clear_reauth(
                    oauth_manager,
                    reauth_service,
                    s.user_id,
                    provider,
                    s.workspace_id or "",
                    checked,
                    cleared,
                )

            # Branch 2: deferred TaskRuns (covers providers with no source).
            for run in awaiting_runs:
                checkpoint = getattr(run, "checkpoint", None) or {}
                provider = (
                    checkpoint.get("awaiting_provider") if isinstance(checkpoint, dict) else None
                )
                if not provider:
                    continue
                await self._maybe_clear_reauth(
                    oauth_manager,
                    reauth_service,
                    run.user_id,
                    provider,
                    run.workspace_id or "",
                    checked,
                    cleared,
                )
        except Exception:
            logger.debug("Re-auth recovery tick error", exc_info=True)

    async def _maybe_clear_reauth(
        self,
        oauth_manager,
        reauth_service,
        user_id: str,
        provider: str,
        workspace_id: str,
        checked: dict[tuple[str, str], str],
        cleared: set[tuple[str, str]],
    ) -> None:
        """Re-check validity for one (user, provider) and clear re-auth if ``ok``.

        De-duped via the shared ``checked``/``cleared`` maps so a provider that
        appears in both the perception-source and TaskRun branches is checked +
        cleared at most once. Fail-open per pair: any error leaves it untouched.
        """
        key = (user_id, provider)
        if key in cleared:
            return
        try:
            reason = checked.get(key)
            if reason is None:
                token = await oauth_manager.get_valid_token_with_reason(user_id, provider)
                reason = token.reason
                checked[key] = reason
            if reason == "ok":
                cleared.add(key)
                await reauth_service.clear_reauth(user_id, provider, workspace_id=workspace_id)
                logger.info(
                    "Re-auth recovery: cleared needs-reauth for %s/%s",
                    user_id,
                    provider,
                )
        except Exception:
            logger.debug(
                "Re-auth recovery check failed for %s/%s",
                user_id,
                provider,
                exc_info=True,
            )

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
