"""Integration lifecycle management — register, poll, health check.

Operates on IntegrationInstallation (the canonical installation model).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.base import CONNECTOR_REGISTRY
from src.models.ids import generate_id
from src.models.integration_installation import IntegrationInstallation
from src.models.observation_cursor import ObservationCursor
from src.services.event_bus import EventBus

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.services.oauth_manager import OAuthManager

logger = logging.getLogger(__name__)

# Connector provider → OAuthManager provider mapping
# Multiple connectors (gmail, calendar, drive) share a single Google OAuth token
_PROVIDER_TO_OAUTH: dict[str, str] = {
    "gmail": "google",
    "calendar": "google",
    "drive": "google",
    "github": "github",
    "slack": "slack",
    "notion": "notion",
}


class IntegrationManager:
    """Manages connector lifecycle: registration, polling, health checks."""

    def __init__(
        self,
        db: AsyncSession,
        event_bus: EventBus | None = None,
        oauth_manager: OAuthManager | None = None,
        settings: Settings | None = None,
    ):
        self._db = db
        self._event_bus = event_bus
        self._oauth_manager = oauth_manager
        self._settings = settings

    async def get_user_connectors(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """List all installations for a user/workspace with status."""
        stmt = select(IntegrationInstallation).where(
            IntegrationInstallation.user_id == user_id,
        )
        if workspace_id:
            stmt = stmt.where(IntegrationInstallation.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        installations = result.scalars().all()
        return [
            {
                "integration_id": inst.install_id,
                "provider": inst.server_name,
                "status": inst.status,
                "health_status": inst.health_status,
                "display_name": inst.display_name,
                "config": inst.config,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
            }
            for inst in installations
        ]

    async def register_integration(
        self, user_id: str, provider: str, config: dict | None = None, workspace_id: str = ""
    ) -> dict:
        """Register a new native connector as a IntegrationInstallation."""
        installation = IntegrationInstallation(
            install_id=generate_id("inst"),
            user_id=user_id,
            workspace_id=workspace_id,
            server_name=provider,
            display_name=provider.replace("_", " ").title(),
            transport="native",
            auth_provider="oauth",
            status="active",
            health_status="unknown",
            config=config or {},
            enabled=True,
        )
        self._db.add(installation)
        await self._db.commit()
        logger.info(
            "Installation registered: %s (%s) for user %s",
            installation.install_id,
            provider,
            user_id,
        )
        return {
            "integration_id": installation.install_id,
            "provider": provider,
            "status": "active",
        }

    async def disconnect(self, install_id: str, user_id: str) -> None:
        """Disconnect (disable) an installation."""
        result = await self._db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.install_id == install_id,
                IntegrationInstallation.user_id == user_id,
            )
        )
        installation = result.scalar_one_or_none()
        if installation:
            installation.status = "disabled"
            installation.enabled = False
            await self._db.commit()

    async def poll_integration(self, install_id: str, user_id: str) -> dict:
        """Run one poll cycle for a specific installation."""
        result = await self._db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.install_id == install_id,
                IntegrationInstallation.user_id == user_id,
            )
        )
        installation = result.scalar_one_or_none()
        if not installation or installation.status != "active":
            return {"events": 0, "error": "Installation not found or inactive"}

        provider = installation.server_name
        connector_cls = CONNECTOR_REGISTRY.get(provider)
        if not connector_cls:
            return {"events": 0, "error": f"No connector implementation for {provider}"}

        # Get credentials
        creds = await self._get_credentials(user_id, provider)
        if not creds:
            return {"events": 0, "error": "No credentials found"}

        # Get cursor
        cursor = await self._get_cursor(user_id, provider, installation.workspace_id)

        # Poll
        from src.connectors.poll_result import PollResult

        instance = connector_cls(settings=self._settings)
        raw = await instance.poll(user_id, cursor, creds)

        # Accept both PollResult (native connectors) and legacy 2-tuples
        # (notion/drive/whatsapp carry a TODO: migrate to PollResult marker).
        if isinstance(raw, PollResult):
            if raw.failed:
                logger.warning(
                    "integration_poll_failed",
                    extra={"provider": provider, "error_class": raw.error_class},
                )
                await self._db.commit()
                return {"events": 0, "provider": provider, "error": raw.error_class}
            events = raw.events
            new_cursor = raw.cursor
        else:
            # Legacy 2-tuple fallback
            events, new_cursor = raw

        # Update cursor only on success
        if new_cursor:
            await self._update_cursor(user_id, provider, new_cursor, installation.workspace_id)

        # Update health status
        installation.health_status = "healthy"

        # Publish events to event bus
        if events and self._event_bus:
            stream = self._event_bus.event_stream(installation.workspace_id)
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
                    workspace_id=installation.workspace_id,
                )

        await self._db.commit()
        logger.info("Polled %s for user %s: %d events", provider, user_id, len(events))
        return {"events": len(events), "provider": provider, "new_cursor": new_cursor}

    async def test_integration(self, install_id: str, user_id: str) -> dict:
        """Test a connector's connection."""
        result = await self._db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.install_id == install_id,
                IntegrationInstallation.user_id == user_id,
            )
        )
        installation = result.scalar_one_or_none()
        if not installation:
            return {"status": "error", "error": "Installation not found"}

        provider = installation.server_name
        connector_cls = CONNECTOR_REGISTRY.get(provider)
        if not connector_cls:
            return {"status": "error", "error": "No implementation"}

        creds = await self._get_credentials(user_id, provider)
        if not creds:
            return {"status": "error", "error": "No credentials"}

        instance = connector_cls(settings=self._settings)
        health = await instance.test(creds)

        # Update health status on the installation
        installation.health_status = "healthy" if health.status == "ok" else "degraded"
        await self._db.flush()

        return {
            "status": health.status,
            "error": health.error,
            "provider": health.provider,
        }

    async def _get_credentials(self, user_id: str, provider: str) -> dict | None:
        """Get decrypted, auto-refreshed OAuth credentials for a provider."""
        if not self._oauth_manager:
            return None

        oauth_provider = _PROVIDER_TO_OAUTH.get(provider, provider)
        access_token = await self._oauth_manager.get_valid_token(user_id, oauth_provider)
        if access_token:
            return {"access_token": access_token}
        return None

    async def _get_cursor(self, user_id: str, provider: str, workspace_id: str) -> str | None:
        """Get the observation cursor for a provider, scoped to the workspace."""
        result = await self._db.execute(
            select(ObservationCursor).where(
                ObservationCursor.workspace_id == workspace_id,
                ObservationCursor.user_id == user_id,
                ObservationCursor.source == provider,
            )
        )
        cursor = result.scalar_one_or_none()
        return cursor.cursor_value if cursor else None

    async def _update_cursor(
        self, user_id: str, provider: str, value: str, workspace_id: str
    ) -> None:
        """Update the observation cursor.

        Uses a single ``INSERT ... ON CONFLICT DO UPDATE`` so this writer and
        the perception-side writer (``JarvisOrchestrator._update_cursor``)
        cannot race on the ``uq_cursor_ws_user_source`` unique constraint.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from ulid import ULID

        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(ObservationCursor)
            .values(
                cursor_id=f"cur_{ULID()}",
                user_id=user_id,
                workspace_id=workspace_id,
                source=provider,
                cursor_type="sync_token",
                cursor_value=value,
                last_observation_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_cursor_ws_user_source",
                set_={
                    "cursor_value": value,
                    "last_observation_at": now,
                },
            )
        )
        await self._db.execute(stmt)
