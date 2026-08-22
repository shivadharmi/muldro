"""Push Units onto the channel the WebSocket route already relays.

`routes_ws.py::relay_pubsub` forwards whatever was JSON-encoded onto
`muldro:a2ui:{user_id}` byte-for-byte, without parsing. So the live half of
transport is a publisher and a client handler; the socket itself changes
nothing.

The message carries `key` at the TOP LEVEL as well as inside the unit. The
client guards on an identity field before dispatching — and the reason that
matters is spec §1: `render_surface` emitted `surface_id` where the hook
expected `id`, so `use-muldro-ws.ts`'s `msg.surface?.id` guard silently
dropped every surface it ever sent, while the tool returned
`{"status": "published"}`. A publisher that does not state its own identity
field where the client reads it is one rename from being invisible again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from src.view.contracts import Unit

logger = logging.getLogger(__name__)

__all__ = ["UNIT_MESSAGE_TYPE", "publish_units", "unit_channel"]

UNIT_MESSAGE_TYPE = "unit"


def unit_channel(user_id: str) -> str:
    """The user's live channel. Same one surfaces used; the payload differs."""
    return f"muldro:a2ui:{user_id}"


async def publish_units(event_bus: Any, units: Sequence[Unit], *, user_id: str) -> None:
    """Publish one message per Unit. Never raises.

    A failed live push costs nothing durable: `GET /v1/workspace/units`
    re-derives the whole feed from the same rows, so the Unit is not lost, it
    is late. Letting the failure propagate would cost the perception poll that
    produced it, which is far worse.

    `event_bus` may be falsy — `EventPublisher.event_bus` is lazily initialised
    and is None until something has called `ensure_event_bus()`. Callers should
    await that accessor, but a None here is a no-op rather than an
    AttributeError thrown into a live poll.
    """
    if not user_id or not units or not event_bus:
        return
    channel = unit_channel(user_id)
    for unit in units:
        try:
            payload = json.dumps(
                {
                    "type": UNIT_MESSAGE_TYPE,
                    "key": unit.frame.key,
                    "unit": unit.model_dump(mode="json"),
                }
            )
            await event_bus.publish_to_channel(channel, payload)
        except Exception as exc:  # noqa: BLE001 - a live push must not cost the poll
            logger.warning(
                "unit_publish_failed key=%s error=%s",
                getattr(getattr(unit, "frame", None), "key", "?"),
                exc,
            )
