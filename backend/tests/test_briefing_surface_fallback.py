"""Tests for briefing surface fallback resolution + empty-state copy (Phase 4, D4).

Covers two related defects:
  * SurfaceService._build_briefing_surface must fall back to the most recent
    briefing when today's hasn't been generated, so the grid card and detail
    tabs agree on the same briefing_id.
  * Briefing detail builders must resolve a briefing even when the opened
    surface is a persisted Presenter surface (id ``surf_...``) carrying no
    briefing_id, falling back to the user's most recent briefing instead of
    printing the confusing "No linked briefing found."
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.surface_builder import SurfaceService
from src.services.surface_detail_builders.briefing import (
    _NO_BRIEFING_YET,
    build_briefing_actions,
    build_briefing_priorities,
)


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


def _persisted_surface(surface_id="surf_abc", user_id="usr_01", workspace_id="ws_01"):
    """A persisted Presenter briefing surface with NO briefing_id in payload."""
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = "briefing"
    s.payload = {}
    s.user_id = user_id
    s.workspace_id = workspace_id
    return s


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
    async def test_returns_none_when_no_briefings_exist(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        surface = await service._build_briefing_surface("usr_01")
        assert surface is None


class TestBriefingDetailFallback:
    @pytest.mark.asyncio
    async def test_priorities_resolves_via_recent_when_no_id(self):
        """Persisted surf_ surface with no briefing_id resolves most-recent."""
        db = AsyncMock()
        briefing = _mock_briefing()
        result = MagicMock()
        result.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result)

        resp = await build_briefing_priorities(db, _persisted_surface())
        assert resp.tab_id == "priorities"
        # Rendered actual priority content, not an empty state.
        flat = [c.id for sec in resp.sections for c in sec.children]
        assert any("pri_" in cid for cid in flat)

    @pytest.mark.asyncio
    async def test_actions_resolves_via_recent_when_no_id(self):
        db = AsyncMock()
        briefing = _mock_briefing()
        result = MagicMock()
        result.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result)

        resp = await build_briefing_actions(db, _persisted_surface())
        assert resp.tab_id == "actions"
        flat = [c.id for sec in resp.sections for c in sec.children]
        assert any("act_" in cid for cid in flat)

    @pytest.mark.asyncio
    async def test_empty_state_copy_when_no_briefing(self):
        """No briefing anywhere → friendly copy, not 'No linked briefing found.'"""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        resp = await build_briefing_priorities(db, _persisted_surface())
        msgs = [c.properties.get("text", "") for sec in resp.sections for c in sec.children]
        assert any(_NO_BRIEFING_YET == m for m in msgs)
        assert all("No linked briefing found." not in m for m in msgs)

    @pytest.mark.asyncio
    async def test_missing_id_target_says_not_found(self):
        """An explicit briefing_id that points at a deleted briefing → 'Briefing not found.'"""
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        surface = MagicMock()
        surface.surface_id = "briefing_gone"
        surface.payload = {}

        resp = await build_briefing_priorities(db, surface)
        msgs = [c.properties.get("text", "") for sec in resp.sections for c in sec.children]
        assert any("Briefing not found." == m for m in msgs)
