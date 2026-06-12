"""User settings service — per-user configuration for policies, budgets, etc."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.users import UserSettings

logger = logging.getLogger(__name__)

# Defaults applied when a setting isn't explicitly configured
SETTING_DEFAULTS = {
    ("policy", "mode"): "approval_required",
    ("budget", "daily_limit_usd"): 5.0,
    ("notification", "channels"): ["web"],
    ("notification", "quiet_hours_start"): None,
    ("notification", "quiet_hours_end"): None,
    ("observation", "gmail_interval_minutes"): 30,
    ("observation", "calendar_interval_minutes"): 180,
    ("observation", "github_interval_minutes"): 60,
    ("observation", "slack_interval_minutes"): 15,
    ("display", "theme"): "dark",
    ("display", "density"): "comfortable",
    ("presentation", "briefing_style"): "general",
    ("privacy", "auto_share_level"): "none",
    ("autonomy", "initiative_level"): "suggest",
}


class SettingsService:
    """Per-user settings backed by the user_settings table."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_all(self, user_id: str) -> dict[str, dict[str, object]]:
        """Get all settings for a user, grouped by category."""
        result = await self._db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        rows = result.scalars().all()

        settings: dict[str, dict[str, object]] = {}
        for row in rows:
            settings.setdefault(row.category, {})[row.key] = row.value

        # Merge defaults for missing keys
        for (category, key), default_value in SETTING_DEFAULTS.items():
            if category not in settings or key not in settings[category]:
                settings.setdefault(category, {})[key] = default_value

        return settings

    async def get(self, user_id: str, category: str, key: str) -> object:
        """Get a single setting value."""
        result = await self._db.execute(
            select(UserSettings.value).where(
                UserSettings.user_id == user_id,
                UserSettings.category == category,
                UserSettings.key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        return SETTING_DEFAULTS.get((category, key))

    async def set(self, user_id: str, category: str, key: str, value: object) -> None:
        """Set a single setting value (atomic upsert).

        Uses ``INSERT ... ON CONFLICT DO UPDATE`` against the unique index
        ``ix_user_settings_unique`` so two concurrent writes for the same
        ``(user_id, category, key)`` cannot both INSERT.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(UserSettings)
            .values(
                user_id=user_id,
                category=category,
                key=key,
                value=value,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "category", "key"],
                set_={"value": value},
            )
        )
        await self._db.execute(stmt)
        await self._db.flush()

    async def delete(self, user_id: str, category: str, key: str) -> None:
        """Delete a setting (reverts to default)."""
        await self._db.execute(
            delete(UserSettings).where(
                UserSettings.user_id == user_id,
                UserSettings.category == category,
                UserSettings.key == key,
            )
        )
        await self._db.flush()

    async def get_policy_mode(self, user_id: str) -> str:
        """Get the user's global policy mode."""
        mode = await self.get(user_id, "policy", "mode")
        return mode or "approval_required"

    async def get_budget_limit(self, user_id: str) -> float:
        """Get daily budget limit in USD."""
        limit = await self.get(user_id, "budget", "daily_limit_usd")
        return float(limit) if limit is not None else 5.0

    async def get_observation_intervals(self, user_id: str) -> dict[str, int]:
        """Get per-source observation polling intervals in minutes."""
        sources = await self.get_observation_sources(user_id)
        return {s["provider"]: s["interval_minutes"] for s in sources if s["enabled"]}

    async def get_observation_sources(self, user_id: str) -> list[dict]:
        """Get configurable observation source list.

        Returns list of {provider, interval_minutes, enabled} dicts.
        Users can add/remove sources via settings API.
        """
        # Check for user-customized sources
        custom = await self.get(user_id, "observation", "sources")
        if isinstance(custom, list) and custom:
            return custom

        # Default sources
        defaults = [
            {"provider": "gmail", "interval_minutes": 30, "enabled": True},
            {"provider": "calendar", "interval_minutes": 180, "enabled": True},
            {"provider": "github", "interval_minutes": 60, "enabled": True},
            {"provider": "slack", "interval_minutes": 15, "enabled": True},
        ]

        # Apply per-source interval overrides from individual settings
        for source in defaults:
            key = f"{source['provider']}_interval_minutes"
            val = await self.get(user_id, "observation", key)
            if val is not None:
                source["interval_minutes"] = int(val)

        return defaults

    async def get_notification_prefs(self, user_id: str) -> dict:
        """Get notification preferences."""
        channels = await self.get(user_id, "notification", "channels")
        quiet_start = await self.get(user_id, "notification", "quiet_hours_start")
        quiet_end = await self.get(user_id, "notification", "quiet_hours_end")
        return {
            "channels": channels or ["web"],
            "quiet_hours_start": quiet_start,
            "quiet_hours_end": quiet_end,
        }
