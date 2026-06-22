"""Memory stability decay and refresh."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.models.memory import Memory

logger = logging.getLogger(__name__)


def _compute_decayed_stability(current_stability: float, days_since_access: int) -> float:
    """Compute new stability score with time-based decay and access boost.

    Formula: min(1.0, max(0.0, current - 0.02 * days) + 0.1)
    - Decays by 0.02 per day since last access
    - Adds 0.1 boost for the current access
    - Clamped to [0.0, 1.0]
    """
    decayed = max(0.0, current_stability - 0.02 * days_since_access)
    return min(1.0, decayed + 0.1)


class MemoryStability:
    """Refresh per-memory stability with time decay and access boost."""

    async def refresh_stability(self, memory_id: str, user_id: str) -> None:
        """Refresh memory stability with time-based decay + access boost.

        Decays stability by 0.02 per day since last access, then adds 0.1.
        This ensures unused memories gradually decay while accessed ones stay stable.
        """
        try:
            now = datetime.now(timezone.utc)

            # Fetch current memory to compute decay
            result = await self._db.execute(select(Memory).where(Memory.memory_id == memory_id))
            memory = result.scalar_one_or_none()
            if not memory:
                return

            last_access = memory.last_accessed_at or memory.created_at
            days_since = (now - last_access).days if last_access else 0
            new_stability = _compute_decayed_stability(memory.stability_score or 0.0, days_since)

            stmt = (
                update(Memory)
                .where(Memory.memory_id == memory_id)
                .values(
                    refresh_count=Memory.refresh_count + 1,
                    last_accessed_at=now,
                    stability_score=new_stability,
                )
            )
            await self._db.execute(stmt)
            await self._db.flush()
            await self._emit_event(
                "memory.updated",
                user_id,
                {"action": "stability_refresh", "memory_id": memory_id},
                workspace_id=getattr(memory, "workspace_id", "") or "",
            )
        except Exception:
            try:
                await self._db.rollback()
            except Exception:
                pass
            logger.debug("Failed to refresh stability for %s", memory_id, exc_info=True)
