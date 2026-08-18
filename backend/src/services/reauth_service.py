"""ReauthService — gate work on OAuth providers the user must reconnect.

When a provider's credential becomes permanently unusable (no_token /
no_refresh_token / revoked), Muldro must stop hammering it and instead prompt
the user to re-authorize. This service is the coordination point:

- ``mark_needs_reauth`` flips the integration to ``needs_reauth``, pauses the
  provider's perception sources, and (deduped) notifies the user.
- ``defer_run`` / ``requeue_deferred_runs`` park and later re-arm autonomous
  runs blocked on the provider.
- ``clear_reauth`` reverses all of the above once the user reconnects.

Collaborators are injected (db_factory, notifier, redis, settings) so the
service stays testable and owns no global state.

This module provides the FOUNDATION + SERVICE only; callers (perception tick,
graph executor, OAuth callback) are wired in a later phase.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.integrations.provider_map import sources_for_provider
from src.services.execution_state import transition_run

logger = logging.getLogger(__name__)

# Re-auth notification dedup TTL (seconds) — suppress repeat prompts for 6h.
_NOTIFY_DEDUP_TTL_S = 6 * 60 * 60

# A friendly display name per provider for user-facing copy.
_PROVIDER_DISPLAY: dict[str, str] = {
    "google": "Google Workspace",
    "github": "GitHub",
    "slack": "Slack",
    "notion": "Notion",
    "atlassian": "Atlassian",
}


def _provider_display(provider: str) -> str:
    return _PROVIDER_DISPLAY.get(provider, provider.title())


def _notify_dedup_key(user_id: str, provider: str) -> str:
    return f"reauth:notified:{user_id}:{provider}"


class ReauthService:
    """Coordinate the "needs re-authorization" lifecycle for a provider."""

    def __init__(self, *, db_factory, notifier, redis=None, settings=None) -> None:
        self._db_factory = db_factory
        self._notifier = notifier
        self._redis = redis
        self._settings = settings

    # ------------------------------------------------------------------
    # Top-level lifecycle
    # ------------------------------------------------------------------

    async def apply_needs_reauth(
        self,
        db,
        user_id: str,
        provider: str,
        reason: str,
    ) -> None:
        """Apply the needs-reauth DB writes on the *passed-in* session ONLY.

        DB WRITES ONLY — no commit, no notify, opens no second session. Sets the
        provider's installation rows to ``status="needs_reauth"`` /
        ``health_status="unavailable"`` and pauses its perception sources
        (``mode="paused"``, ``last_error="needs_reauth"``).

        This is the lock-safe primitive a caller already holding row locks (e.g.
        the perception tick, which has ``SELECT ... FOR UPDATE`` on the same
        ``perception_state`` rows) can call inside its own transaction. The
        caller owns the commit; ``notify_reauth`` must run post-commit.

        ``reason`` is accepted for symmetry/logging parity with the notify path
        but only the durable status writes happen here.
        """
        await self._set_installation_state(
            db, user_id, provider, status="needs_reauth", health_status="unavailable"
        )
        await self.pause_perception_sources(db, user_id, provider)

    async def mark_needs_reauth(
        self,
        user_id: str,
        provider: str,
        reason: str,
        workspace_id: str = "",
        *,
        notify: bool = True,
    ) -> None:
        """Flip the provider's integration to ``needs_reauth`` + pause sources.

        Thin convenience over :meth:`apply_needs_reauth`: opens its own session,
        applies the durable writes, commits, then (when ``notify``) sends a
        dedup'd reconnect prompt post-commit. Use this from a caller that is NOT
        already inside a transaction touching the same rows; a caller holding
        row locks must use :meth:`apply_needs_reauth` on its own session instead
        to avoid a cross-session self-deadlock.
        """
        async with self._db_factory() as db:
            await self.apply_needs_reauth(db, user_id, provider, reason)
            await db.commit()

        if notify:
            await self.notify_reauth(user_id, provider, reason, workspace_id=workspace_id)

    async def clear_reauth(
        self,
        user_id: str,
        provider: str,
        workspace_id: str = "",
    ) -> None:
        """Reverse a needs-reauth state once the user reconnects.

        Restores the installation to ``active``/``healthy``, resumes perception
        sources, requeues deferred runs, and clears the notify-dedup key.
        """
        async with self._db_factory() as db:
            await self._set_installation_state(
                db, user_id, provider, status="active", health_status="healthy"
            )
            await self.resume_perception_sources(db, user_id, provider)
            await self.requeue_deferred_runs(db, user_id, provider)
            await db.commit()

        if self._redis is not None:
            try:
                await self._redis.delete(_notify_dedup_key(user_id, provider))
            except Exception:
                logger.debug("Failed to clear reauth dedup key", exc_info=True)

    # ------------------------------------------------------------------
    # Installation state
    # ------------------------------------------------------------------

    async def _set_installation_state(
        self,
        db,
        user_id: str,
        provider: str,
        *,
        status: str,
        health_status: str,
    ) -> None:
        """Set status/health on every installation row for ``provider``."""
        from src.integrations.provider_map import servers_for_provider
        from src.models.integration_installation import IntegrationInstallation

        servers = servers_for_provider(provider)
        result = await db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.user_id == user_id,
                IntegrationInstallation.server_name.in_(servers),
            )
        )
        for inst in result.scalars().all():
            inst.status = status
            inst.health_status = health_status

    # ------------------------------------------------------------------
    # Perception sources
    # ------------------------------------------------------------------

    async def pause_perception_sources(self, db, user_id: str, provider: str) -> None:
        """Pause every perception source backed by ``provider``.

        Sets ``mode="paused"`` and records ``last_error="needs_reauth"`` so the
        scheduler's ``get_due_sources`` excludes them.
        """
        for pstate in await self._provider_states(db, user_id, provider):
            pstate.mode = "paused"
            pstate.last_error = "needs_reauth"

    async def resume_perception_sources(self, db, user_id: str, provider: str) -> None:
        """Resume perception sources after the provider is reconnected.

        Sets ``mode="poll"``, flags ``pending_run`` so the next tick fires
        immediately, and clears the circuit breaker.
        """
        for pstate in await self._provider_states(db, user_id, provider):
            pstate.mode = "poll"
            pstate.pending_run = True
            pstate.circuit_state = "closed"
            pstate.consecutive_failures = 0
            pstate.last_error = None

    async def _provider_states(self, db, user_id: str, provider: str) -> list:
        from src.models.perception_state import PerceptionState

        sources = sources_for_provider(provider)
        result = await db.execute(
            select(PerceptionState).where(
                PerceptionState.user_id == user_id,
                PerceptionState.source.in_(sources),
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Deferred runs
    # ------------------------------------------------------------------

    async def defer_run(self, db, run, provider: str) -> None:
        """Park a running run on the provider it is blocked on.

        Transitions the run to ``awaiting_reauth`` (via the state machine) and
        stores the blocking provider in the run's durable ``checkpoint`` bag so
        ``requeue_deferred_runs`` can find it later.
        """
        transition_run(run, "awaiting_reauth")
        checkpoint = dict(run.checkpoint or {})
        checkpoint["awaiting_provider"] = provider
        run.checkpoint = checkpoint

    async def requeue_deferred_runs(self, db, user_id: str, provider: str) -> int:
        """Transition this user's runs deferred on ``provider`` back to pending.

        The background-task scheduler tick then re-picks them up. That tick only
        selects ``source in ("background", "approval_resume")`` — autonomous
        runs from the Governor carry ``source="plan"`` and would be orphaned
        forever once requeued. So when a deferred run's source is not already
        tick-visible we flip it to ``"background"`` (the tick path populates
        steps and executes via ``execute_run``). Returns the number of runs
        requeued.
        """
        from src.models.task_graph import TaskRun

        result = await db.execute(
            select(TaskRun).where(
                TaskRun.user_id == user_id,
                TaskRun.status == "awaiting_reauth",
            )
        )
        count = 0
        for run in result.scalars().all():
            checkpoint = run.checkpoint or {}
            if checkpoint.get("awaiting_provider") != provider:
                continue
            transition_run(run, "pending")
            if run.source not in ("background", "approval_resume"):
                run.source = "background"
            count += 1
        return count

    # ------------------------------------------------------------------
    # Notification
    # ------------------------------------------------------------------

    async def notify_reauth(
        self,
        user_id: str,
        provider: str,
        reason: str,
        workspace_id: str = "",
    ) -> None:
        """Send a dedup'd "reconnect" prompt for ``provider``.

        Deduped via a Redis SET-NX key (6h TTL): only the first prompt within
        the window is delivered. Builds an alert A2UI surface and delivers it as
        a ``critical_alert`` (bypasses rate limits, broadcasts to all surfaces).
        """
        if not await self._claim_notify_slot(user_id, provider):
            logger.debug("reauth notify suppressed by dedup for %s/%s", user_id, provider)
            return

        display = _provider_display(provider)
        surface = self._build_reauth_surface(provider, display)
        await self._notifier.notify(
            user_id,
            "critical_alert",
            title=f"Reconnect {display}",
            body=f"Muldro lost access to {display}. Re-authorize to resume.",
            data={
                "kind": "reauth",
                "provider": provider,
                "reason": reason,
                "reconnect_url": f"/v1/auth/{provider}/start",
                "surface": surface,
            },
            workspace_id=workspace_id,
        )

    async def _claim_notify_slot(self, user_id: str, provider: str) -> bool:
        """Atomically claim the dedup slot. Returns True if we may notify.

        With no Redis configured we always notify (no dedup available).
        """
        if self._redis is None:
            return True
        try:
            acquired = await self._redis.set(
                _notify_dedup_key(user_id, provider),
                "1",
                nx=True,
                ex=_NOTIFY_DEDUP_TTL_S,
            )
            return bool(acquired)
        except Exception:
            # Fail-open on a Redis hiccup: better to risk a duplicate prompt
            # than to swallow a needed reconnect alert.
            logger.debug("reauth dedup claim failed; notifying anyway", exc_info=True)
            return True

    def _build_reauth_surface(self, provider: str, display: str) -> dict:
        """Build a minimal alert A2UI surface prompting reconnect."""
        from src.ui.contracts import A2UISurface
        from src.ui.renderer import alert, button, card, text

        children = [
            alert(
                id="reauth_alert",
                message=f"Muldro lost access to {display}.",
                severity="error",
                title="Reconnect required",
            ),
            text(
                id="reauth_body",
                text=f"Re-authorize {display} to resume perception and actions.",
            ),
            button(
                id="reauth_reconnect",
                label=f"Reconnect {display}",
                variant="primary",
                action_payload={
                    "action": "reconnect_integration",
                    "provider": provider,
                    "url": f"/v1/auth/{provider}/start",
                },
            ),
        ]
        surface = A2UISurface(
            id=f"reauth_{provider}",
            children=[card(id="reauth_card", children=children)],
            metadata={"kind": "alert", "title": f"Reconnect {display}", "provider": provider},
        )
        return surface.model_dump()
