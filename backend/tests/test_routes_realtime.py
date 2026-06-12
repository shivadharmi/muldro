"""Tests for realtime SSE routes — focus on run-stream ownership (IDOR guard)."""

from unittest.mock import MagicMock

import pytest


class _FakeResult:
    """Mimics the result of db.execute() for scalar_one_or_none()."""

    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


@pytest.mark.asyncio
async def test_stream_run_progress_rejects_run_owned_by_other_user():
    """A run that does not belong to the requesting user must yield 404.

    Regression guard for the IDOR where any authenticated user could stream
    `jarvis:run:{run_id}` for an arbitrary run_id without an ownership check.
    """
    from fastapi import HTTPException

    from src.api.routes_realtime import stream_run_progress

    # db returns no row when filtered by (run_id, user_id) → not owned by caller.
    async def fake_execute(_stmt):
        return _FakeResult(scalar=None)

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    request = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await stream_run_progress(
            run_id="run_owned_by_someone_else",
            request=request,
            user_id="usr_attacker",
            db=mock_db,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_run_progress_allows_owned_run():
    """A run owned by the requesting user streams (no ownership rejection)."""
    from src.api.routes_realtime import stream_run_progress

    owned_run = MagicMock()
    owned_run.run_id = "run_mine"
    owned_run.user_id = "usr_owner"

    async def fake_execute(_stmt):
        return _FakeResult(scalar=owned_run)

    mock_db = MagicMock()
    mock_db.execute = fake_execute

    # Provide a redis so the route proceeds past the availability guard.
    request = MagicMock()
    request.app.state.redis = MagicMock()

    # Should not raise — returns a StreamingResponse.
    response = await stream_run_progress(
        run_id="run_mine",
        request=request,
        user_id="usr_owner",
        db=mock_db,
    )
    assert response.media_type == "text/event-stream"
