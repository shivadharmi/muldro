"""Deferred-read verification tick (spec §4.5 async fast-path loop).

Re-checks completed_unverified steps with a give-up TTL. On confirmation: upgrade to
completed + fire the DEFERRED trust increment (trust graduates only on verified
writes). On post-turn divergence: partially_completed + async-divergence surface via
the Notifier hold-for-briefing path (the user may be absent). Past the TTL: stop
re-checking — the step stays completed_unverified (a success, permanently unconfirmed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_step
from src.services.risk_assessor import record_approval_decision
from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import ReadBackVerifier, VerifyVerdict

logger = logging.getLogger(__name__)

# Eventual-consistency window before the FIRST re-check, and the give-up ceiling.
DEFERRED_VERIFICATION_MIN_AGE_S = 60.0
DEFERRED_VERIFICATION_TTL_S = 3600.0  # 1h — stop re-checking after this


def _age_seconds(step, *, now: datetime) -> float:
    completed_at = getattr(step, "completed_at", None)
    if completed_at is None:
        return 0.0
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - completed_at).total_seconds())


def _is_past_give_up_ttl(step, *, now: datetime) -> bool:
    return _age_seconds(step, now=now) > DEFERRED_VERIFICATION_TTL_S


def _should_recheck(step, now: datetime) -> bool:
    """The two loop guards, as one pure predicate: re-check a step only once it is
    past the eventual-consistency window (MIN_AGE) and not yet past the give-up TTL.

    Younger than MIN_AGE → too soon (the write may not have propagated). Past the TTL
    → give up (the step stays completed_unverified — a success, permanently unconfirmed).
    """
    age = _age_seconds(step, now=now)
    if age < DEFERRED_VERIFICATION_MIN_AGE_S:
        return False
    if _is_past_give_up_ttl(step, now=now):
        return False
    return True


async def _apply_recheck(db, run, step, verdict: VerifyVerdict, *, notifier) -> None:
    """Apply a re-check verdict to a completed_unverified step."""
    meta = (step.output_data or {}).get("verification", {})
    capability = meta.get("capability") or (step.input_data or {}).get("capability", "")

    # Post-action reconciliation feed (spec §4.5): raise on confirmed, lower on
    # contradicted. Abstention feed only — never the gate (§4.3). Best-effort.
    if verdict in (VerifyVerdict.CONFIRMED, VerifyVerdict.CONTRADICTED):
        try:
            from src.services.entity_facts.reconciliation import reconcile_verdict

            await reconcile_verdict(
                db,
                workspace_id=run.workspace_id or "",
                user_id=run.user_id,
                verdict=verdict,
                write_input=step.input_data or {},
                write_output=step.output_data or {},
            )
        except Exception:
            logger.debug("world-model reconciliation failed (deferred tick)", exc_info=True)

    if verdict == VerifyVerdict.CONFIRMED:
        transition_step(step, "completed")
        # Deferred trust increment: trust graduates now that the write is verified.
        # A confirmed auto-execution is a positive outcome ("approved"). This is the
        # DB-only free function record_approval_decision touches — no TrustGate (and no
        # Anthropic client / SurfaceEmitter) needed just to reach it. Best-effort: a
        # trust write must never fail an otherwise-successful confirmation.
        if capability:
            try:
                # SAVEPOINT: a failed trust write (e.g. a flush inside
                # record_approval_decision) must roll back only this nested
                # transaction, never poison the shared session's later flush/commit
                # (mirrors the Step-4 reconcile fix). A best-effort trust increment
                # must never fail an otherwise-successful confirmation.
                async with db.begin_nested():
                    await record_approval_decision(
                        db,
                        run.workspace_id or "",
                        capability,
                        meta.get("risk_level", "high"),
                        # Honor the user's decision_type when a human-approved write is
                        # confirmed here (stamped by dag_runner). Auto-exec writes carry
                        # none → default "approved".
                        meta.get("decision_type", "approved"),
                    )
            except Exception:
                logger.debug("Deferred trust increment failed for %s", step.step_id, exc_info=True)
        await db.flush()
        return

    if verdict == VerifyVerdict.CONTRADICTED:
        transition_step(step, "partially_completed")
        await db.flush()
        # Async-divergence surface via hold-for-briefing (user may be absent).
        escalation = build_divergence_escalation(
            capability=capability,
            artifact_ref=meta.get("artifact_ref") or {},
            observed="Post-turn read-back could not confirm this write's effect.",
        )
        if notifier is not None:
            try:
                # Notifier.notify(user_id, notification_type, title, body, data, workspace_id).
                # verification_divergence is not a bypass type and priority defaults to
                # 0.5 (< 0.6), so the notification takes the hold-for-briefing path
                # rather than interrupting an absent user. It is also persisted to the
                # notifications table (retrievable via the notifications API).
                await notifier.notify(
                    user_id=run.user_id,
                    notification_type="verification_divergence",
                    title=f"Could not confirm {capability}",
                    body=escalation["observed"],
                    data={**escalation, "run_id": run.run_id, "step_id": step.step_id},
                    workspace_id=run.workspace_id or "",
                )
            except Exception:
                logger.warning("Failed to raise async-divergence surface", exc_info=True)
        return

    # UNVERIFIED — leave completed_unverified; the next tick retries until TTL.
    return


class DeferredVerificationTickMixin:
    """Re-checks completed_unverified steps (spec §4.5). Confirmed -> completed +
    deferred trust increment; contradicted -> partially_completed + async surface."""

    async def _tick_deferred_verification(self, factory) -> None:
        now = datetime.now(timezone.utc)
        try:
            async with factory() as db:
                result = await db.execute(
                    select(TaskStep).where(TaskStep.status == "completed_unverified")
                )
                steps = [s for s in result.scalars().all() if _should_recheck(s, now)]
                if not steps:
                    return

                notifier = self._resolve_deferred_notifier(db)
                verifier = self._build_deferred_verifier(db)

                for step in steps:
                    run = (
                        await db.execute(select(TaskRun).where(TaskRun.run_id == step.run_id))
                    ).scalar_one_or_none()
                    if run is None:
                        continue
                    meta = (step.output_data or {}).get("verification", {})
                    verdict = await verifier.verify_step(
                        capability=meta.get("capability", ""),
                        write_input=step.input_data or {},
                        write_output=step.output_data or {},
                        risk=_Risk(meta),
                    )
                    await _apply_recheck(db, run, step, verdict, notifier=notifier)
                await db.commit()
        except Exception:
            logger.warning("Deferred verification tick failed", exc_info=True)

    def _resolve_deferred_notifier(self, db):
        """Resolve a Notifier for the async-divergence surface.

        Preferred: reuse the orchestrator's already-wired notifier (built with a live
        redis client, so hold-for-briefing + rate-limiting actually work) — the same
        source sibling ticks use (``services.notifier``). Fallback: build one from
        ``settings.redis_url`` so ``_hold_for_briefing`` genuinely buffers. Returns None
        only if no redis is reachable at all (the divergence transition still happens;
        only the surface push is skipped)."""
        services = getattr(self._orchestrator, "_services", None) if self._orchestrator else None
        wired = getattr(services, "notifier", None) if services else None
        if wired is not None:
            return wired

        try:
            import redis.asyncio as aioredis

            from src.services.notifier import Notifier
            from src.services.surface_registry import SurfaceRegistry

            redis = aioredis.from_url(self._settings.redis_url, decode_responses=True)
            return Notifier(surface_registry=SurfaceRegistry(redis=redis), redis=redis, db=db)
        except Exception:
            logger.debug("Notifier unavailable for deferred verification tick", exc_info=True)
            return None

    def _build_deferred_verifier(self, db) -> ReadBackVerifier:
        """Build the re-check verifier. read_fn reuses the same read path as the inline
        gate (step_runner.run_readback); if unavailable in this context, read_fn=None
        means POST_CONDITIONS caps stay unverified until they age out (safe)."""
        return ReadBackVerifier(read_fn=None)


class _Risk:
    """Reconstruct a RiskAssessment-shaped object from the persisted verification meta
    so the verifier's irreversibility union sees the original (reversible, blast_radius)."""

    def __init__(self, meta: dict):
        self.reversible = meta.get("reversible", False)
        self.blast_radius = meta.get("blast_radius", "self")
        self.risk_level = meta.get("risk_level", "high")
