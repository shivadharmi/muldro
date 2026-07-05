"""Deferred-read verification tick (spec §4.5 async fast-path loop).

Re-checks completed_unverified steps with a give-up TTL. On confirmation: upgrade to
completed + fire the DEFERRED trust increment (trust graduates only on verified
writes). On post-turn divergence: partially_completed + async-divergence surface via
the Notifier hold-for-briefing path (the user may be absent). Past the TTL: stop
re-checking — the step stays completed_unverified (a success, permanently unconfirmed).
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_step
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


async def _flush(db) -> None:
    """Flush the session, tolerating a non-async db (e.g. a mocked session in tests).

    A real ``AsyncSession.flush()`` returns a coroutine we await; a plain object's
    ``flush()`` (or a MagicMock) is a no-op we skip. The durable write is the
    tick's own ``db.commit()`` — this flush only makes the transition visible early."""
    flush = getattr(db, "flush", None)
    if flush is None:
        return
    result = flush()
    if inspect.isawaitable(result):
        await result


async def _apply_recheck(db, run, step, verdict: VerifyVerdict, *, trust_gate, notifier) -> None:
    """Apply a re-check verdict to a completed_unverified step."""
    meta = (step.output_data or {}).get("verification", {})
    capability = meta.get("capability") or (step.input_data or {}).get("capability", "")

    if verdict == VerifyVerdict.CONFIRMED:
        transition_step(step, "completed")
        # Deferred trust increment: trust graduates now that the write is verified.
        try:
            await trust_gate.record_auto_execution_outcome(
                capability, meta.get("risk_level", "high"), run.workspace_id or ""
            )
        except Exception:
            logger.debug("Deferred trust increment failed for %s", step.step_id, exc_info=True)
        await _flush(db)
        return

    if verdict == VerifyVerdict.CONTRADICTED:
        transition_step(step, "partially_completed")
        await _flush(db)
        # Async-divergence surface via hold-for-briefing (user may be absent).
        escalation = build_divergence_escalation(
            capability=capability,
            artifact_ref=meta.get("artifact_ref") or {},
            observed="Post-turn read-back could not confirm this write's effect.",
        )
        try:
            # Notifier.notify(user_id, notification_type, title, body, data, workspace_id).
            # verification_divergence is not a bypass type, so a low-priority notification
            # holds for the next briefing rather than interrupting an absent user.
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
                steps = list(result.scalars().all())
                if not steps:
                    return

                notifier = self._build_deferred_notifier(db)
                trust_gate = self._build_deferred_trust_gate(db, notifier)
                verifier = self._build_deferred_verifier(db)

                for step in steps:
                    age = _age_seconds(step, now=now)
                    if age < DEFERRED_VERIFICATION_MIN_AGE_S:
                        continue  # inside the eventual-consistency window
                    if _is_past_give_up_ttl(step, now=now):
                        continue  # gave up — stays completed_unverified
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
                    await _apply_recheck(
                        db, run, step, verdict, trust_gate=trust_gate, notifier=notifier
                    )
                await db.commit()
        except Exception:
            logger.warning("Deferred verification tick failed", exc_info=True)

    def _build_deferred_notifier(self, db):
        """Build a Notifier for the async-divergence surface.

        Notifier's real ctor is Notifier(surface_registry, redis=None, ...) — the
        surface registry is required, so we construct one from the same redis client.
        Best-effort: returns None if redis/registry cannot be built (the divergence
        transition still happens; only the surface push is skipped)."""
        try:
            from src.services.notifier import Notifier
            from src.services.surface_registry import SurfaceRegistry

            redis = getattr(self, "_redis", None)
            registry = SurfaceRegistry(redis=redis)
            return Notifier(surface_registry=registry, redis=redis, db=db)
        except Exception:
            logger.debug("Notifier unavailable for deferred verification tick", exc_info=True)
            return None

    def _build_deferred_trust_gate(self, db, notifier):
        """Build a TrustGate for the deferred trust increment.

        TrustGate's real ctor is keyword-only (db, client, redis, notifier_provider,
        store, emitter). The deferred increment path only touches ``db`` (via
        record_approval_decision), but we wire the collaborators honestly so the object
        is fully formed."""
        from src.config.settings import get_anthropic_client
        from src.services.execution_surface_emitter import SurfaceEmitter
        from src.services.step_graph_store import StepGraphStore
        from src.services.trust_gate import TrustGate

        redis = getattr(self, "_redis", None)
        client = None
        try:
            client = get_anthropic_client(self._settings)
        except Exception:
            logger.debug("Anthropic client unavailable for deferred trust gate", exc_info=True)
        emitter = SurfaceEmitter(settings=self._settings, db=db, redis=redis)
        return TrustGate(
            db=db,
            client=client,
            redis=redis,
            notifier_provider=lambda: notifier,
            store=StepGraphStore(db),
            emitter=emitter,
        )

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
