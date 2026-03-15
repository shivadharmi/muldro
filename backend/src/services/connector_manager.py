"""Connector lifecycle management — register, poll, health check."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.connectors.base import CONNECTOR_REGISTRY
from src.models.connectors import Connector
from src.models.observation_cursor import ObservationCursor
from src.models.users import OAuthConnection
from src.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class ConnectorManager:
    """Manages connector lifecycle: registration, polling, health checks."""

    def __init__(self, db: AsyncSession, event_bus: EventBus | None = None):
        self._db = db
        self._event_bus = event_bus

    async def get_user_connectors(self, user_id: str) -> list[dict]:
        """List all connectors for a user with status."""
        result = await self._db.execute(select(Connector).where(Connector.user_id == user_id))
        connectors = result.scalars().all()
        return [
            {
                "connector_id": c.connector_id,
                "provider": c.provider,
                "status": c.status,
                "config": c.config,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in connectors
        ]

    async def register_connector(
        self, user_id: str, provider: str, config: dict | None = None
    ) -> dict:
        """Register a new connector for a user."""
        connector_id = f"conn_{ULID()}"
        connector = Connector(
            connector_id=connector_id,
            user_id=user_id,
            provider=provider,
            status="active",
            config=config or {},
        )
        self._db.add(connector)
        await self._db.commit()
        logger.info("Connector registered: %s (%s) for user %s", connector_id, provider, user_id)
        return {
            "connector_id": connector_id,
            "provider": provider,
            "status": "active",
        }

    async def disconnect(self, connector_id: str, user_id: str) -> None:
        """Disconnect (deactivate) a connector."""
        result = await self._db.execute(
            select(Connector).where(
                Connector.connector_id == connector_id,
                Connector.user_id == user_id,
            )
        )
        connector = result.scalar_one_or_none()
        if connector:
            connector.status = "disconnected"
            await self._db.commit()

    async def poll_connector(self, connector_id: str, user_id: str) -> dict:
        """Run one poll cycle for a specific connector."""
        result = await self._db.execute(
            select(Connector).where(
                Connector.connector_id == connector_id,
                Connector.user_id == user_id,
            )
        )
        connector = result.scalar_one_or_none()
        if not connector or connector.status != "active":
            return {"events": 0, "error": "Connector not found or inactive"}

        provider = connector.provider
        connector_cls = CONNECTOR_REGISTRY.get(provider)
        if not connector_cls:
            return {"events": 0, "error": f"No connector implementation for {provider}"}

        # Get credentials
        creds = await self._get_credentials(user_id, provider)
        if not creds:
            return {"events": 0, "error": "No credentials found"}

        # Get cursor
        cursor = await self._get_cursor(user_id, provider)

        # Poll
        instance = connector_cls()
        events, new_cursor = await instance.poll(user_id, cursor, creds)

        # Update cursor
        if new_cursor:
            await self._update_cursor(user_id, provider, new_cursor)

        # Publish events to event bus
        if events and self._event_bus:
            stream = self._event_bus.event_stream(user_id)
            for event in events:
                await self._event_bus.publish(
                    stream,
                    event.event_type,
                    {
                        "source": event.source,
                        "entity_type": event.entity_type,
                        "entity_id": event.entity_id,
                        "title": event.title,
                        "summary": event.summary,
                        "actor": event.actor,
                    },
                    user_id=user_id,
                )

        await self._db.commit()
        logger.info("Polled %s for user %s: %d events", provider, user_id, len(events))
        return {"events": len(events), "provider": provider, "new_cursor": new_cursor}

    async def test_connector(self, connector_id: str, user_id: str) -> dict:
        """Test a connector's connection."""
        result = await self._db.execute(
            select(Connector).where(
                Connector.connector_id == connector_id,
                Connector.user_id == user_id,
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            return {"status": "error", "error": "Connector not found"}

        connector_cls = CONNECTOR_REGISTRY.get(connector.provider)
        if not connector_cls:
            return {"status": "error", "error": "No implementation"}

        creds = await self._get_credentials(user_id, connector.provider)
        if not creds:
            return {"status": "error", "error": "No credentials"}

        instance = connector_cls()
        health = await instance.test(creds)
        return {
            "status": health.status,
            "error": health.error,
            "provider": health.provider,
        }

    async def _get_credentials(self, user_id: str, provider: str) -> dict | None:
        """Get OAuth credentials for a provider."""
        result = await self._db.execute(
            select(OAuthConnection).where(
                OAuthConnection.user_id == user_id,
                OAuthConnection.provider == provider,
            )
        )
        conn = result.scalar_one_or_none()
        if not conn:
            return None
        return {
            "access_token": conn.access_token_encrypted,  # TODO: decrypt
            "refresh_token": conn.refresh_token_encrypted,
        }

    async def _get_cursor(self, user_id: str, provider: str) -> str | None:
        """Get the observation cursor for a provider."""
        result = await self._db.execute(
            select(ObservationCursor).where(
                ObservationCursor.user_id == user_id,
                ObservationCursor.source == provider,
            )
        )
        cursor = result.scalar_one_or_none()
        return cursor.cursor_value if cursor else None

    async def _update_cursor(self, user_id: str, provider: str, value: str) -> None:
        """Update the observation cursor."""
        result = await self._db.execute(
            select(ObservationCursor).where(
                ObservationCursor.user_id == user_id,
                ObservationCursor.source == provider,
            )
        )
        cursor = result.scalar_one_or_none()
        if cursor:
            cursor.cursor_value = value
            cursor.updated_at = datetime.now(timezone.utc)
        else:
            self._db.add(
                ObservationCursor(
                    cursor_id=f"cur_{ULID()}",
                    user_id=user_id,
                    source=provider,
                    cursor_value=value,
                )
            )
