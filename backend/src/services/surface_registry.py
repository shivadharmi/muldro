"""Track active user surfaces (web, API).

Redis-backed registry of which surfaces a user is currently connected to.
Used by the Notifier to decide where to deliver messages and avoid duplicates.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Surface presence TTL — if no heartbeat within this window, surface is stale
SURFACE_TTL_SECONDS = 120  # 2 minutes for web (WebSocket heartbeat)


@dataclass
class SurfaceInfo:
    surface: str  # web, api
    connected_at: str
    last_heartbeat: str
    metadata: dict


class SurfaceRegistry:
    """Track which surfaces a user is active on."""

    def __init__(self, redis=None):
        self._redis = redis
        # In-memory fallback when Redis unavailable
        self._local: dict[str, dict[str, SurfaceInfo]] = {}

    def _key(self, user_id: str) -> str:
        return f"jarvis:surfaces:{user_id}"

    async def register(
        self,
        user_id: str,
        surface: str,
        metadata: dict | None = None,
    ) -> None:
        """Register a surface as active for a user."""
        now = datetime.now(timezone.utc).isoformat()
        info = {
            "surface": surface,
            "connected_at": now,
            "last_heartbeat": now,
            "metadata": metadata or {},
        }

        if self._redis:
            await self._redis.hset(self._key(user_id), surface, json.dumps(info))
            await self._redis.expire(self._key(user_id), SURFACE_TTL_SECONDS)
        else:
            self._local.setdefault(user_id, {})[surface] = SurfaceInfo(**info)

        logger.info(
            "surface_registered",
            extra={"user_id": user_id, "surface": surface},
        )

    async def unregister(self, user_id: str, surface: str) -> None:
        """Unregister a surface (e.g., WebSocket disconnect)."""
        if self._redis:
            await self._redis.hdel(self._key(user_id), surface)
        else:
            self._local.get(user_id, {}).pop(surface, None)

        logger.info(
            "surface_unregistered",
            extra={"user_id": user_id, "surface": surface},
        )

    async def heartbeat(self, user_id: str, surface: str) -> None:
        """Update the heartbeat timestamp for a surface."""
        if self._redis:
            raw = await self._redis.hget(self._key(user_id), surface)
            if raw:
                info = json.loads(raw)
                info["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                await self._redis.hset(self._key(user_id), surface, json.dumps(info))
                await self._redis.expire(self._key(user_id), SURFACE_TTL_SECONDS)
        else:
            user_surfaces = self._local.get(user_id, {})
            if surface in user_surfaces:
                user_surfaces[surface].last_heartbeat = datetime.now(timezone.utc).isoformat()

    async def get_active_surfaces(self, user_id: str) -> list[str]:
        """Get list of active surface names for a user."""
        if self._redis:
            data = await self._redis.hgetall(self._key(user_id))
            return list(data.keys()) if data else []
        return list(self._local.get(user_id, {}).keys())

    async def get_surface_info(self, user_id: str, surface: str) -> SurfaceInfo | None:
        """Get details about a specific surface connection."""
        if self._redis:
            raw = await self._redis.hget(self._key(user_id), surface)
            if raw:
                return SurfaceInfo(**json.loads(raw))
            return None
        user_surfaces = self._local.get(user_id, {})
        return user_surfaces.get(surface)

    async def get_preferred_surface(self, user_id: str) -> str | None:
        """Determine the best surface for notification delivery.

        Priority: web (less intrusive) first.
        User preference memories can override this in the future.
        """
        surfaces = await self.get_active_surfaces(user_id)
        if not surfaces:
            return None
        if "web" in surfaces:
            return "web"
        return surfaces[0]

    async def is_active(self, user_id: str, surface: str) -> bool:
        """Check if a specific surface is currently active."""
        surfaces = await self.get_active_surfaces(user_id)
        return surface in surfaces
