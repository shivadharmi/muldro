from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapter.openconnector_admin_client import (
    OpenConnectorAdminClient,
    OpenConnectorAdminError,
)


def _resp(status, json_body):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_body)
    return r


async def test_start_authorization_posts_service_and_connection_name():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    fake = AsyncMock()
    fake.post = AsyncMock(
        return_value=_resp(
            200, {"service": "gmail", "authorizationUrl": "https://x", "state": "s1"}
        )
    )
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "src.adapter.openconnector_admin_client.httpx.AsyncClient", MagicMock(return_value=fake)
    ):
        out = await client.start_authorization(service="gmail", connection_name="ws:u:gmail:work")

    assert out["authorizationUrl"] == "https://x"
    assert out["state"] == "s1"
    _, kwargs = fake.post.call_args
    assert kwargs["json"] == {"service": "gmail", "connectionName": "ws:u:gmail:work"}
    assert kwargs["headers"]["Authorization"] == "Bearer t"


async def test_list_connections_returns_rows():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    fake = AsyncMock()
    fake.get = AsyncMock(
        return_value=_resp(
            200,
            [
                {
                    "id": "gmail:ws:u:gmail:work",
                    "connectionName": "ws:u:gmail:work",
                    "configured": True,
                }
            ],
        )
    )
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "src.adapter.openconnector_admin_client.httpx.AsyncClient", MagicMock(return_value=fake)
    ):
        rows = await client.list_connections()

    assert rows[0]["connectionName"] == "ws:u:gmail:work"
    assert rows[0]["configured"] is True


async def test_non_2xx_raises():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    fake = AsyncMock()
    fake.post = AsyncMock(return_value=_resp(401, {"error": {"code": "unauthorized"}}))
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "src.adapter.openconnector_admin_client.httpx.AsyncClient", MagicMock(return_value=fake)
    ):
        with pytest.raises(OpenConnectorAdminError):
            await client.start_authorization(service="gmail", connection_name="c")
