from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.openconnector_admin_client import (
    OpenConnectorAdminClient,
    OpenConnectorAdminError,
)

_PATCH = "src.services.openconnector_admin_client.httpx.AsyncClient"


def _resp(status, json_body, text="{}"):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_body)
    r.text = text
    return r


def _fake_http(**methods):
    fake = AsyncMock()
    for name, retval in methods.items():
        setattr(fake, name, AsyncMock(return_value=retval))
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    return fake


async def test_start_authorization_posts_service_and_connection_name():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    fake = _fake_http(
        post=_resp(200, {"service": "gmail", "authorizationUrl": "https://x", "state": "s1"})
    )
    with patch(_PATCH, MagicMock(return_value=fake)):
        out = await client.start_authorization(service="gmail", connection_name="ws:u:gmail:work")

    assert out["authorizationUrl"] == "https://x"
    assert out["state"] == "s1"
    args, kwargs = fake.post.call_args
    assert args[0].endswith("/api/oauth/authorizations")
    assert kwargs["json"] == {"service": "gmail", "connectionName": "ws:u:gmail:work"}
    assert kwargs["headers"]["Authorization"] == "Bearer t"


async def test_list_connections_returns_rows():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    seed = [{"id": "gmail:c", "connectionName": "ws:u:gmail:work", "configured": True}]
    fake = _fake_http(get=_resp(200, seed))
    with patch(_PATCH, MagicMock(return_value=fake)):
        rows = await client.list_connections()

    args, _ = fake.get.call_args
    assert args[0].endswith("/api/connections")
    assert rows[0]["connectionName"] == "ws:u:gmail:work"
    assert rows[0]["configured"] is True


async def test_base_url_trailing_slash_is_normalized():
    client = OpenConnectorAdminClient(base_url="http://oc:3000/", admin_token="t")
    fake = _fake_http(get=_resp(200, []))
    with patch(_PATCH, MagicMock(return_value=fake)):
        await client.list_connections()

    args, _ = fake.get.call_args
    assert args[0] == "http://oc:3000/api/connections"


async def test_non_2xx_raises():
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    err = {"error": {"code": "unauthorized"}}
    fake = _fake_http(post=_resp(401, err, text='{"error":"unauthorized"}'))
    with patch(_PATCH, MagicMock(return_value=fake)):
        with pytest.raises(OpenConnectorAdminError):
            await client.start_authorization(service="gmail", connection_name="c")


async def test_non_json_error_body_still_raises_admin_error():
    """A 502 with an HTML body must raise OpenConnectorAdminError, not JSONDecodeError.

    The error path uses resp.text (not resp.json()); a mock whose .json() raises
    would blow up the old code but must be tolerated now.
    """
    client = OpenConnectorAdminClient(base_url="http://oc:3000", admin_token="t")
    bad = MagicMock()
    bad.status_code = 502
    bad.json = MagicMock(side_effect=ValueError("no json"))
    bad.text = "<html>502 Bad Gateway</html>"
    fake = _fake_http(get=bad)
    with patch(_PATCH, MagicMock(return_value=fake)):
        with pytest.raises(OpenConnectorAdminError):
            await client.list_connections()
