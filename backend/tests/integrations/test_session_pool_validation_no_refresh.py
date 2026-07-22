"""Regression: a client-side arg VALIDATION error must NOT tear down the session.

A malformed tool call (bad/missing args, e.g. query_freebusy without time_min/
time_max) raises a client-side validation error. On an OAuth server (e.g.
google-workspace) this was triggering ``refresh_session``, which closes the
*shared* session and releases the managed uvx process — cascading
"Session task completed unexpectedly" to every concurrent/subsequent calendar
call (get_events, list_calendars) until the circuit breaker opened.

A validation error is the *agent's* fault (bad args), not the session's;
refreshing the OAuth token/session does nothing useful and destabilizes a
healthy shared session.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool


def _oauth_pool() -> UserMCPSessionPool:
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "google-workspace",
        {
            "transport": "stdio",
            "auth_provider": "google",  # -> _is_oauth_server True
            "command": "uvx",
            "args": ["workspace-mcp"],
        },
        workspace_id="ws_1",
    )
    return pool


def _session_raising(exc: Exception) -> MagicMock:
    client = AsyncMock()
    client.call_tool = AsyncMock(side_effect=exc)
    session = MagicMock()
    session.client = client
    return session


async def test_validation_error_does_not_refresh_oauth_session():
    pool = _oauth_pool()
    session = _session_raising(ValueError("2 validation errors: time_min Missing required arg"))

    with (
        patch.object(pool, "get_or_create_session", AsyncMock(return_value=session)),
        patch.object(pool, "refresh_session", AsyncMock()) as refresh_mock,
    ):
        result = await pool.call_tool(
            "query_freebusy",
            {},
            user_id="u1",
            server_name="google-workspace",
            workspace_id="ws_1",
        )

    assert result["status"] == "error"
    # The bad-args validation error must NOT have torn down the shared session.
    refresh_mock.assert_not_awaited()


async def test_not_found_error_does_not_refresh_oauth_session():
    pool = _oauth_pool()
    session = _session_raising(RuntimeError("404 not found: calendar does not exist"))

    with (
        patch.object(pool, "get_or_create_session", AsyncMock(return_value=session)),
        patch.object(pool, "refresh_session", AsyncMock()) as refresh_mock,
    ):
        result = await pool.call_tool(
            "get_events",
            {"calendar_id": "nope"},
            user_id="u1",
            server_name="google-workspace",
            workspace_id="ws_1",
        )

    assert result["status"] == "error"
    refresh_mock.assert_not_awaited()


async def test_session_lost_rebuilds_session_and_retries():
    """A dead session ('Session task completed unexpectedly') is transient: the
    retry loop refreshes + re-acquires a fresh session, then the retry succeeds."""
    pool = _oauth_pool()

    dead = _session_raising(RuntimeError("Session task completed unexpectedly"))

    ok_result = MagicMock()
    ok_result.content = [MagicMock(text="events-json")]
    good_client = AsyncMock()
    good_client.call_tool = AsyncMock(return_value=ok_result)
    good = MagicMock()
    good.client = good_client

    get_session = AsyncMock(side_effect=[dead, good])
    with (
        patch.object(pool, "get_or_create_session", get_session),
        patch.object(pool, "refresh_session", AsyncMock()) as refresh_mock,
        patch("asyncio.sleep", AsyncMock()),
    ):
        result = await pool.call_tool(
            "get_events",
            {},
            user_id="u1",
            server_name="google-workspace",
            workspace_id="ws_1",
        )

    assert result["status"] == "ok"
    assert result["result"] == "events-json"
    refresh_mock.assert_awaited()  # dead session was rebuilt
    assert get_session.await_count == 2  # pre-loop acquire + re-acquire after refresh


async def test_auth_error_still_refreshes_oauth_session():
    """Guard: a genuine AUTH error MUST still refresh (stale-bearer recovery)."""
    pool = _oauth_pool()
    session = _session_raising(RuntimeError("401 unauthorized"))

    with (
        patch.object(pool, "get_or_create_session", AsyncMock(return_value=session)),
        patch.object(pool, "refresh_session", AsyncMock()) as refresh_mock,
    ):
        await pool.call_tool(
            "get_events",
            {},
            user_id="u1",
            server_name="google-workspace",
            workspace_id="ws_1",
        )

    refresh_mock.assert_awaited()
