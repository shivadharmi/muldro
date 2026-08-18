from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes_connections import BeginConnectionRequest, begin, confirm


async def test_begin_returns_authorization_url():
    svc = AsyncMock()
    svc.begin_connection = AsyncMock(return_value="https://consent")
    db = AsyncMock()
    with patch("src.api.routes_connections.ConnectionService", return_value=svc):
        out = await begin(
            BeginConnectionRequest(provider="gmail", alias="work"),
            workspace_id="ws1",
            user_id="usrA",
            db=db,
        )
    assert out.authorization_url == "https://consent"
    svc.begin_connection.assert_awaited_once_with(
        db, workspace_id="ws1", principal_id="usrA", provider="gmail", alias="work"
    )
    db.commit.assert_awaited_once()


async def test_confirm_returns_status():
    svc = AsyncMock()
    svc.confirm_connection = AsyncMock(return_value=True)
    db = AsyncMock()
    with patch("src.api.routes_connections.ConnectionService", return_value=svc):
        out = await confirm(
            BeginConnectionRequest(provider="gmail", alias="work"),
            workspace_id="ws1",
            user_id="usrA",
            db=db,
        )
    assert out.status == "active"
    db.commit.assert_awaited_once()


async def test_confirm_pending_maps_to_pending_status():
    svc = AsyncMock()
    svc.confirm_connection = AsyncMock(return_value=False)
    db = AsyncMock()
    with patch("src.api.routes_connections.ConnectionService", return_value=svc):
        out = await confirm(
            BeginConnectionRequest(provider="gmail", alias="work"),
            workspace_id="ws1",
            user_id="usrA",
            db=db,
        )
    assert out.status == "pending"


async def test_begin_unconfigured_gateway_raises_503():
    db = AsyncMock()
    with patch(
        "src.api.routes_connections.ConnectionService",
        side_effect=RuntimeError("not configured"),
    ):
        with pytest.raises(HTTPException) as exc:
            await begin(
                BeginConnectionRequest(provider="gmail", alias="work"),
                workspace_id="ws1",
                user_id="usrA",
                db=db,
            )
    assert exc.value.status_code == 503
