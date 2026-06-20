"""Tests for the Google Drive connector — PollResult, 410 reinit, pagination, removed files."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.drive_connector import DriveConnector
from src.connectors.poll_result import PollResult
from tests.conftest import TEST_USER_ID, make_mock_settings


def _make_drive_file(file_id: str, name: str = "Doc", trashed: bool | None = None) -> dict:
    """Build a minimal Drive file resource for a change."""
    info = {
        "id": file_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-06-21T10:00:00Z",
        "lastModifyingUser": {"emailAddress": "alice@example.com", "displayName": "Alice"},
        "webViewLink": f"https://drive.google.com/file/{file_id}",
    }
    if trashed is not None:
        info["trashed"] = trashed
    return info


def _mock_client(get_side_effect):
    """Build a mocked httpx.AsyncClient context manager whose .get yields responses."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=get_side_effect)
    return mock_client


@pytest.mark.asyncio
async def test_drive_returns_pollresult():
    """A successful incremental poll returns a PollResult instance."""
    connector = DriveConnector(make_mock_settings())

    page = MagicMock()
    page.status_code = 200
    page.json.return_value = {
        "newStartPageToken": "token_999",
        "changes": [{"fileId": "f1", "file": _make_drive_file("f1")}],
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([page])
        result = await connector.poll(TEST_USER_ID, "token_1", {"access_token": "tok"})

    assert isinstance(result, PollResult)
    assert result.ok is True
    assert result.cursor == "token_999"
    assert {e.entity_id for e in result.events} == {"f1"}


@pytest.mark.asyncio
async def test_drive_410_reinitializes_page_token():
    """A 410 on changes.list (expired pageToken) must re-init via startPageToken, not stall.

    Mirrors calendar's 410 -> full re-sync. The connector re-fetches a fresh
    startPageToken and recovers (a success with a fresh token), NOT a silent
    empty poll that leaves the connector permanently stalled.
    """
    connector = DriveConnector(make_mock_settings())

    incoming_cursor = "expired_token"

    # 1. changes.list with expired pageToken -> 410.
    changes_410 = MagicMock()
    changes_410.status_code = 410

    # 2. recurse with cursor=None -> first-poll path: files.list -> 200.
    files_resp = MagicMock()
    files_resp.status_code = 200
    files_resp.json.return_value = {"files": [_make_drive_file("f_new", "Fresh")]}

    # 3. changes/startPageToken -> 200, fresh token.
    start_token_resp = MagicMock()
    start_token_resp.status_code = 200
    start_token_resp.json.return_value = {"startPageToken": "fresh_token_42"}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([changes_410, files_resp, start_token_resp])
        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.ok is True
    # Recovered with a fresh token — NOT silently stuck on the expired one.
    assert result.cursor == "fresh_token_42"
    assert result.cursor != incoming_cursor


@pytest.mark.asyncio
async def test_drive_paginates_changes():
    """poll() follows nextPageToken and takes newStartPageToken from the final page only."""
    connector = DriveConnector(make_mock_settings())

    incoming_cursor = "page_token_start"
    final_token = "newStart_final_777"

    # Page 1: two changes + nextPageToken, NO newStartPageToken.
    page1 = MagicMock()
    page1.status_code = 200
    page1.json.return_value = {
        "nextPageToken": "page2tok",
        "changes": [
            {"fileId": "c1", "file": _make_drive_file("c1", "A")},
            {"fileId": "c2", "file": _make_drive_file("c2", "B")},
        ],
    }
    # Page 2 (final): one more change + newStartPageToken, NO nextPageToken.
    page2 = MagicMock()
    page2.status_code = 200
    page2.json.return_value = {
        "newStartPageToken": final_token,
        "changes": [{"fileId": "c3", "file": _make_drive_file("c3", "C")}],
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client([page1, page2])
        mock_cls.return_value = mock_client
        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.ok is True
    # ALL changes across both pages collected.
    assert {e.entity_id for e in result.events} == {"c1", "c2", "c3"}
    # Cursor advances to the final newStartPageToken, never the old one.
    assert result.cursor == final_token
    assert result.cursor != incoming_cursor

    # Second request carried the pageToken from page 1.
    second_params = mock_client.get.call_args_list[1].kwargs["params"]
    assert second_params.get("pageToken") == "page2tok"


@pytest.mark.asyncio
async def test_drive_removed_file_emits_event():
    """A change with removed=true (no file) emits a file_removed RawEvent keyed on fileId."""
    connector = DriveConnector(make_mock_settings())

    page = MagicMock()
    page.status_code = 200
    page.json.return_value = {
        "newStartPageToken": "token_after",
        "changes": [
            {"fileId": "gone_1", "removed": True},
            {"fileId": "live_1", "file": _make_drive_file("live_1", "Alive")},
        ],
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([page])
        result = await connector.poll(TEST_USER_ID, "tok_1", {"access_token": "tok"})

    assert result.ok is True
    by_id = {e.entity_id: e for e in result.events}
    assert "gone_1" in by_id
    assert by_id["gone_1"].event_type == "file_removed"
    assert by_id["live_1"].event_type == "file_modified"


@pytest.mark.asyncio
async def test_drive_trashed_file_emits_removed_event():
    """A change whose file.trashed is true emits a file_removed event."""
    connector = DriveConnector(make_mock_settings())

    page = MagicMock()
    page.status_code = 200
    page.json.return_value = {
        "newStartPageToken": "token_after",
        "changes": [
            {"fileId": "trash_1", "file": _make_drive_file("trash_1", "Trashed", trashed=True)},
        ],
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([page])
        result = await connector.poll(TEST_USER_ID, "tok_1", {"access_token": "tok"})

    assert result.ok is True
    by_id = {e.entity_id: e for e in result.events}
    assert by_id["trash_1"].event_type == "file_removed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_class",
    [
        (401, "auth_failed"),
        (403, "auth_failed"),
        (429, "rate_limited"),
        (500, "transient"),
        (503, "transient"),
        (400, "permanent"),
    ],
)
async def test_drive_http_error_classes(status_code, expected_class):
    """Non-200 incremental responses map to the right error_class; cursor unchanged."""
    connector = DriveConnector(make_mock_settings())
    incoming_cursor = "cursor_keepme"

    err_resp = MagicMock()
    err_resp.status_code = status_code

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([err_resp])
        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.failed is True
    assert result.error_class == expected_class
    assert result.cursor == incoming_cursor
    assert result.events == []


@pytest.mark.asyncio
async def test_drive_exception_is_transient():
    """An unexpected exception during poll maps to transient; cursor unchanged."""
    connector = DriveConnector(make_mock_settings())
    incoming_cursor = "cursor_keepme"

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client(RuntimeError("boom"))
        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.failed is True
    assert result.error_class == "transient"
    assert result.cursor == incoming_cursor
    assert result.events == []


@pytest.mark.asyncio
async def test_drive_no_access_token_is_auth_failed():
    """Missing access token returns auth_failed with unchanged cursor."""
    connector = DriveConnector(make_mock_settings())
    result = await connector.poll(TEST_USER_ID, "cursor_x", {})

    assert isinstance(result, PollResult)
    assert result.error_class == "auth_failed"
    assert result.cursor == "cursor_x"
