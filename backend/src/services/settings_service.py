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
        """Set a single setting value (upsert)."""
        result = await self._db.execute(
            select(UserSettings).where(
                UserSettings.user_id == user_id,
                UserSettings.category == category,
                UserSettings.key == key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
        else:
            self._db.add(
                UserSettings(
                    user_id=user_id,
                    category=category,
                    key=key,
                    value=value,
                )
            )
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
        intervals = {}
        for source in ("gmail", "calendar", "github", "slack"):
            key = f"{source}_interval_minutes"
            val = await self.get(user_id, "observation", key)
            intervals[source] = (
                int(val) if val is not None else SETTING_DEFAULTS.get(("observation", key), 60)
            )
        return intervals

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
