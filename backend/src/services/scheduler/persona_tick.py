"""Persona-agent batch over recent interactions (every 10th tick)."""

import logging
from datetime import datetime, timedelta, timezone

from src.models.database import get_session_factory

logger = logging.getLogger(__name__)


def _format_interaction(i) -> str:
    """Render one InteractionLog row as a full trace line (not a lossy summary).

    Carries timestamp + message_preview + intent + plan_summary + response_preview
    so Persona learns from the whole recorded interaction, not just message->intent.
    Fields are skipped gracefully when empty.
    """
    created_at = getattr(i, "created_at", None)
    when = f"{created_at:%Y-%m-%d %H:%M}" if created_at else "unknown-time"
    parts = [f"[{when}] user: {i.message_preview or '(no preview)'}"]
    if i.intent:
        parts.append(f"intent={i.intent}")
    if i.plan_summary:
        parts.append(f"plan: {i.plan_summary}")
    if i.response_preview:
        parts.append(f"muldro: {i.response_preview}")
    return " | ".join(parts)


class PersonaTickMixin:
    """Batches recent interactions to the Persona agent every 10th tick."""

    async def _tick_persona_batch(self, factory=None) -> None:
        """Run Persona agent on recent interactions every 10th tick (~5 min).

        Only fires when there are 5+ interactions since last batch.
        """
        if getattr(self, "_tick_count", 0) % 10 != 0:
            return
        if not self._orchestrator:
            return

        try:
            factory = factory or get_session_factory()
            async with factory() as db:
                from sqlalchemy import select

                from src.models.interaction_log import InteractionLog

                last_batch = getattr(self, "_last_persona_batch_at", None)
                if last_batch is None:
                    last_batch = datetime.now(timezone.utc) - timedelta(hours=24)
                query = (
                    select(InteractionLog)
                    .where(InteractionLog.created_at > last_batch)
                    .order_by(InteractionLog.created_at.desc())
                    .limit(50)  # budget-bounded window, was 20 (Step 7A T1)
                )

                result = await db.execute(query)
                interactions = result.scalars().all()

                if len(interactions) < 5:
                    return

                # Group by (workspace_id, user_id) to avoid cross-workspace mixing
                grouped: dict[tuple[str, str], list] = {}
                for i in interactions:
                    key = (getattr(i, "workspace_id", "") or "", i.user_id)
                    grouped.setdefault(key, []).append(i)

                for (ws_id, uid), group in grouped.items():
                    if len(group) < 5:
                        continue
                    trace = "\n".join(_format_interaction(i) for i in group)
                    await self._orchestrator._call_agent(
                        "persona",
                        message=(
                            "Analyze the recent interaction trace and extract"
                            f" preference patterns:\n{trace}"
                        ),
                        user_id=uid,
                        workspace_id=ws_id,
                    )

                self._last_persona_batch_at = datetime.now(timezone.utc)
                logger.info("Persona batch completed: %d interactions analyzed", len(interactions))

        except Exception:
            logger.warning("Persona batch tick failed", exc_info=True)
