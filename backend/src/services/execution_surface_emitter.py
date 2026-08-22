"""Surface and event emission for the graph executor.

Extracted from ``GraphExecutor`` (SVC-P1-3): the executor is a frozen god
object, so its cohesive Redis/event-bus emission cluster lives here as an
injected collaborator. This module owns the best-effort publishing of domain
events, WebSocket run-progress, and live ``SurfaceUpdate`` streaming (with a
durable DB fallback).

It is a leaf under ``src.services`` — it must never import ``graph_executor``.
All datastore/transport dependencies are injected; nothing is reached back out
of the hub.
"""

from __future__ import annotations

import json
import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class SurfaceEmitter:
    """Best-effort surface/event publisher for graph execution.

    Holds the same datastore/transport handles the executor was injected with
    (``event_bus``, ``redis``, ``db``, ``db_factory``, ``settings``); every
    method is best-effort and never raises into the execution path, EXCEPT
    ``emit_event(..., durable=True)``, which deliberately propagates a persist
    failure so a state-recording event's transaction aborts atomically
    (Step 5 §4.8).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db,
        event_bus=None,
        redis=None,
        db_factory=None,
    ):
        self._settings = settings
        self._db = db
        self._event_bus = event_bus
        self._redis = redis
        self._db_factory = db_factory

    async def emit_event(
        self,
        event_type: str,
        user_id: str,
        payload: dict,
        workspace_id: str | None = None,
        durable: bool = False,
    ) -> None:
        """Publish a domain event (best-effort) + Redis progress + DB persistence.

        ``durable=True`` marks a *state-recording* event (the runtime_events log is
        the system-of-record, Step 5 §4.8): a DB-persist failure PROPAGATES so the
        enclosing state-change transaction aborts atomically rather than leaving the
        event log with a silent gap. Redis publishing stays best-effort in both modes.
        """
        if self._event_bus:
            try:
                stream = self._event_bus.agent_stream(workspace_id or "")
                await self._event_bus.publish(
                    stream, event_type, payload, user_id, workspace_id=workspace_id or ""
                )
            except Exception:
                logger.debug("Failed to emit %s event", event_type, exc_info=True)

        # Persist to runtime_events table (system-of-record for home feed / activity).
        run_id = payload.get("run_id")
        step_id = payload.get("step_id")
        if workspace_id:
            from src.models.runtime_event import RuntimeEvent

            self._db.add(
                RuntimeEvent(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    step_id=step_id,
                    event_type=event_type.replace(".", "_"),
                    payload=payload,
                )
            )
            if durable:
                # System-of-record: never silently gap. A persist failure must abort
                # the state-change transaction (do NOT catch — let it propagate).
                await self._db.flush()
            else:
                try:
                    await self._db.flush()
                except Exception:
                    logger.debug("Failed to persist runtime event %s", event_type, exc_info=True)

        # Publish to Redis for WebSocket progress streaming
        if run_id:
            await self.publish_progress(run_id, {"event_type": event_type, **payload})

    async def publish_progress(self, run_id: str, data: dict) -> None:
        """Publish step progress to Redis pubsub for WebSocket consumers."""
        try:
            channel = f"muldro:run_progress:{run_id}"
            payload = json.dumps(data)

            if self._redis:
                await self._redis.publish(channel, payload)
            else:
                import redis.asyncio as aioredis

                redis = aioredis.from_url(self._settings.redis_url)
                try:
                    await redis.publish(channel, payload)
                finally:
                    await redis.aclose()
        except Exception:
            logger.debug("Failed to publish run progress", exc_info=True)

    async def emit_surface_update(
        self,
        surface_id: str | None,
        user_id: str,
        phase: str,
        steps: list | None = None,
        current_step: str | None = None,
        progress: str = "",
        approval: object | None = None,
        results: object | None = None,
        workspace_id: str | None = None,
        tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Publish a SurfaceUpdate to Redis for live workspace streaming.

        Also persists the latest surface state to the DB as a durable fallback
        so reconnecting clients can recover missed updates.

        ``tokens``/``cost_usd`` carry the run's usage rollup on terminal frames
        (completed/failed); live frames may omit them.

        Best-effort — failures are logged but never raised.
        """
        if not surface_id:
            return

        try:
            from src.contracts import SurfaceUpdate

            update = SurfaceUpdate(
                surface_id=surface_id,
                phase=phase,
                steps=steps or [],
                current_step=current_step,
                progress=progress,
                approval=approval,
                results=results,
                tokens=tokens,
                cost_usd=cost_usd,
            )

            channel = f"muldro:a2ui:{user_id}"
            payload = json.dumps(
                {
                    "type": "surface_update",
                    **update.model_dump(mode="json"),
                }
            )

            if self._redis:
                await self._redis.publish(channel, payload)
            elif self._event_bus:
                await self._event_bus.publish_to_channel(channel, payload)
        except Exception:
            logger.debug("Failed to emit surface update", exc_info=True)

        # Durable fallback: persist latest surface state to DB so reconnecting
        # clients can recover updates that were missed while disconnected.
        try:
            if self._db_factory and workspace_id:
                from sqlalchemy import select

                from src.models.ui_state import UISurface

                async with self._db_factory() as persist_db:
                    result = await persist_db.execute(
                        select(UISurface).where(UISurface.surface_id == surface_id)
                    )
                    existing = result.scalar_one_or_none()

                    from src.contracts import SurfaceUpdate

                    surface_data = SurfaceUpdate(
                        surface_id=surface_id,
                        phase=phase,
                        steps=steps or [],
                        current_step=current_step,
                        progress=progress,
                        approval=approval,
                        results=results,
                        tokens=tokens,
                        cost_usd=cost_usd,
                    ).model_dump(mode="json")

                    # The unified run surface id IS the run_id; persist it in the
                    # payload so the detail builders' explicit-linkage path
                    # (_extract_run_id) resolves the run without relying on
                    # surface_id derivation. Self-describing rows.
                    run_meta = {"run_id": surface_id}
                    if existing:
                        existing.payload = {
                            **(existing.payload or {}),
                            "metadata": {
                                **(existing.payload or {}).get("metadata", {}),
                                **run_meta,
                            },
                            "last_surface_update": surface_data,
                        }
                        # Normalize legacy "execution" kinds to "run" so the
                        # frontend dispatches to the unified run renderer.
                        if existing.surface_type in ("execution", "plan"):
                            existing.surface_type = "run"
                    else:
                        persist_db.add(
                            UISurface(
                                surface_id=surface_id,
                                user_id=user_id,
                                workspace_id=workspace_id,
                                surface_type="run",
                                payload={
                                    "metadata": run_meta,
                                    "last_surface_update": surface_data,
                                },
                            )
                        )
                    await persist_db.commit()
        except Exception:
            logger.debug("Failed to persist surface update to DB", exc_info=True)
