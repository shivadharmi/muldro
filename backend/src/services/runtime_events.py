"""Runtime event emitter — canonical event emission for all execution transitions.

Canonical event taxonomy:
  command_received     — user submitted a command/message
  route_selected       — orchestrator chose agent route
  plan_created         — planner produced a structured plan
  run_created          — TaskRun created from a plan
  step_started         — TaskStep execution began
  step_completed       — TaskStep finished successfully
  step_failed          — TaskStep failed
  step_blocked         — TaskStep blocked on dependency
  approval_requested   — approval gate triggered
  approval_resolved    — approval granted or rejected
  tool_call_started    — tool execution began
  tool_call_completed  — tool execution succeeded
  tool_call_failed     — tool execution failed
  fallback_selected    — capability resolver fell back to secondary backend
  artifact_created     — artifact/surface produced
  surface_created      — dynamic UI surface emitted
  run_completed        — TaskRun finished successfully
  run_failed           — TaskRun failed
  run_cancelled        — TaskRun cancelled
  agent_started        — sub-agent began processing
  agent_completed      — sub-agent finished processing

Perception events:
  perception_started          — perception cycle began for a source
  perception_completed        — perception cycle finished
  perception_skipped          — cycle skipped (budget, circuit, etc.)
  perception_signal_received  — webhook/intent/agent signal arrived
  perception_policy_updated   — agent-informed policy applied
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models.runtime_event import RuntimeEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class RuntimeEventEmitter:
    """Emits runtime events to both DB (durable) and Redis (realtime)."""

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._event_bus = event_bus

    async def emit(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
        user_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Emit a runtime event to DB + event bus (best-effort)."""
        event_payload = payload or {}

        # Persist to DB
        try:
            self._db.add(
                RuntimeEvent(
                    workspace_id=self._workspace_id,
                    run_id=run_id,
                    step_id=step_id,
                    event_type=event_type,
                    payload=event_payload,
                )
            )
            await self._db.flush()
        except Exception:
            logger.debug("Failed to persist runtime event %s", event_type, exc_info=True)

        # Publish to Redis for realtime subscribers
        if self._event_bus and user_id:
            try:
                stream = self._event_bus.agent_stream(self._workspace_id)
                await self._event_bus.publish(
                    stream,
                    event_type,
                    event_payload,
                    user_id,
                    workspace_id=self._workspace_id,
                )
            except Exception:
                logger.debug("Failed to publish runtime event %s", event_type, exc_info=True)
