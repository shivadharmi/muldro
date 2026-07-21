"""SurfacePusher.push_briefing_surface — the live-push half of the briefing
dedup fix (Task 1, Subsystem B).

Exercises the REAL method (mocked event bus + db_factory only — no facade
mocking) so the actual dedup id format, the WS publish channel/payload, and
the ui_surfaces persistence are all verified. This is the exact mechanism
Task 1 exists to fix: before this fix, the live push minted a different
surface_id (surf_<ULID>) than the REST rebuild (briefing_<id>), producing two
cards for one briefing.

Mirrors the pattern in test_dev3_surface_id_wiring.py::test_returns_surface_id_on_success
(same mocked event bus + db_factory shape, for push_workspace_surface).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.ids import ensure_prefix
from src.orchestrator.surface_pusher import SurfacePusher


def _briefing(**kw):
    base = dict(
        briefing_id="brief_01ABC",
        headline="Your Tuesday",
        top_priorities=[{"title": "Pay LIC premium"}, {"title": "Reply to investor"}],
        recommended_actions=[{"title": "Draft reply"}],
        created_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestPushBriefingSurface:
    @pytest.mark.asyncio
    async def test_returns_deduped_surface_id_and_publishes_structured_card(self):
        # Mock event bus with Redis that returns integer from incr (rate limit check).
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_event_bus = AsyncMock()
        mock_event_bus._redis = mock_redis
        mock_event_bus.publish_to_channel = AsyncMock()
        events = MagicMock()
        events.ensure_event_bus = AsyncMock(return_value=mock_event_bus)

        # Mock DB persistence (inner context manager) — captures the UISurface add().
        db_factory = MagicMock()
        mock_db = AsyncMock()
        db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        sp = SurfacePusher(events, lambda: db_factory)

        briefing = _briefing()

        result = await sp.push_briefing_surface(
            briefing,
            user_id="usr_01",
            workspace_id="ws_01",
        )

        # (3) Dedup guarantee: byte-identical to SurfaceService._build_briefing_surface's
        # f"briefing_{briefing.briefing_id}" AND to ensure_prefix("briefing", briefing_id).
        assert result == ensure_prefix("briefing", briefing.briefing_id)
        assert result == "briefing_brief_01ABC"
        assert result == f"briefing_{briefing.briefing_id}"

        # (4) WS publish to the per-user a2ui channel, structured "surface" frame.
        mock_event_bus.publish_to_channel.assert_awaited_once()
        channel, ws_msg = mock_event_bus.publish_to_channel.call_args.args
        assert channel == "jarvis:a2ui:usr_01"
        payload = json.loads(ws_msg)
        assert payload["type"] == "surface"
        assert payload["surface"]["kind"] == "briefing"
        assert payload["surface"]["id"] == result

        # (5) Persisted UISurface is the structured path, not a markdown blob:
        # non-empty items/metrics prove build_briefing_preview ran, not a plan text dump.
        mock_db.add.assert_called_once()
        persisted = mock_db.add.call_args.args[0]
        assert persisted.surface_type == "briefing"
        assert persisted.surface_id == result
        assert persisted.preview["items"] == ["Pay LIC premium", "Reply to investor"]
        assert {m["label"]: m["value"] for m in persisted.preview["metrics"]} == {
            "Priorities": "2",
            "Actions": "1",
        }

    @pytest.mark.asyncio
    async def test_rate_limited_returns_none_and_does_not_publish(self):
        """When check_surface_rate denies, no WS publish and no DB write happen."""
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=999)  # over the workspace limit (5/min)
        mock_redis.expire = AsyncMock()
        mock_event_bus = AsyncMock()
        mock_event_bus._redis = mock_redis
        mock_event_bus.publish_to_channel = AsyncMock()
        events = MagicMock()
        events.ensure_event_bus = AsyncMock(return_value=mock_event_bus)

        db_factory = MagicMock()
        sp = SurfacePusher(events, lambda: db_factory)

        result = await sp.push_briefing_surface(
            _briefing(),
            user_id="usr_01",
            workspace_id="ws_01",
        )

        assert result is None
        mock_event_bus.publish_to_channel.assert_not_awaited()
