"""Briefing surface fallback resolution.

``SurfaceService._build_briefing_surface`` must fall back to the most recent briefing
when today's hasn't been generated, so the card points at a briefing_id that resolves
rather than at a day with nothing behind it.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.surface_builder import SurfaceService


def _mock_briefing(
    briefing_id: str = "brief_01",
    briefing_date: date | None = None,
) -> MagicMock:
    b = MagicMock()
    b.briefing_id = briefing_id
    b.user_id = "usr_01"
    b.workspace_id = "ws_01"
    b.briefing_date = briefing_date or date.today()
    b.headline = "Daily Briefing"
    b.top_priorities = [{"title": "Ship Phase 4", "why": "user impact"}]
    b.recommended_actions = [{"title": "Review PR", "description": "before merge"}]
    b.created_at = datetime.now(timezone.utc)
    return b


class TestBuildBriefingSurfaceFallback:
    @pytest.mark.asyncio
    async def test_prefers_todays_briefing(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")
        today_briefing = _mock_briefing("brief_today")

        result = MagicMock()
        result.scalar_one_or_none.return_value = today_briefing
        db.execute = AsyncMock(return_value=result)

        surface = await service._build_briefing_surface("usr_01")
        assert surface is not None
        assert surface.id == "briefing_brief_today"
        # Only one query needed when today's briefing exists.
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_most_recent_when_no_today(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")
        yesterday = _mock_briefing("brief_yesterday", date(2026, 6, 20))

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = None  # no today
            else:
                result.scalar_one_or_none.return_value = yesterday  # most recent
            return result

        db.execute = mock_execute

        surface = await service._build_briefing_surface("usr_01")
        assert surface is not None
        # Card now points at the resolvable most-recent briefing id.
        assert surface.id == "briefing_brief_yesterday"

    @pytest.mark.asyncio
    async def test_carries_priority_items(self):
        """Briefing preview exposes top_priorities as items[] (capped at 5)."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")
        briefing = _mock_briefing("brief_items")
        briefing.top_priorities = [{"title": f"Priority {i}", "why": "x"} for i in range(7)]

        result = MagicMock()
        result.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result)

        surface = await service._build_briefing_surface("usr_01")
        assert surface is not None
        items = surface.preview["items"]
        assert len(items) == 5  # capped
        assert items[0] == "Priority 0"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_briefings_exist(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        surface = await service._build_briefing_surface("usr_01")
        assert surface is None
