import logging
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from src.adapter.enforcement import GMAIL_PROFILE, GatewayProfile
from src.adapter.warm_start import _param_names_from_guide, register_gateway_tools
from src.integrations.gateway_actions.gmail import GMAIL_ACTIONS
from src.integrations.gateway_naming import action_id_to_tool_name

_BY_ID = {a.action_id: a for a in GMAIL_ACTIONS}
_EMPTY_GUIDE = {"data": {"markdown": ""}}


def _guide(markdown: str) -> dict:
    """A guide payload shaped like OpenConnector's get_action_guide response."""
    return {"data": {"markdown": markdown, "capability": {}}}


_FETCH_EMAILS_MD = """\
## Input Parameters

| Name    | Required | Type     |
| ------- | -------- | -------- |
| `query` | No       | `string` |
| `labelIds` | No    | `array`  |
| `includeSpamTrash` | No | `boolean` |
| `detail` | No      | `"ids" \\| "summary" \\| "full"` |
| `maxResults` | No  | `integer` |
| `pageToken` | No   | `string` |

## Current Connection
"""


def test_param_names_from_guide_extracts_the_table_column():
    names = _param_names_from_guide(_guide(_FETCH_EMAILS_MD))
    assert names == {"query", "labelIds", "includeSpamTrash", "detail", "maxResults", "pageToken"}


def test_param_names_from_guide_extracts_table_column():
    md = (
        "## Input Parameters\n\n"
        "| Name | Required | Type |\n| ---- | -------- | ---- |\n"
        "| `query` | No | `string` |\n| `maxResults` | No | `integer` |\n"
    )
    assert _param_names_from_guide({"data": {"markdown": md}}) == {"query", "maxResults"}


def test_param_names_from_guide_returns_empty_when_no_markdown():
    assert _param_names_from_guide({"nope": 1}) == set()


async def test_registers_agent_legal_names_not_dotted():
    adapter = FastMCP("t")
    await register_gateway_tools(
        adapter, GMAIL_PROFILE, guide_fetcher=AsyncMock(return_value=_EMPTY_GUIDE)
    )
    names = {t.name for t in await adapter.list_tools()}
    assert "gmail.get_profile" not in names  # dotted (illegal) must NOT be exposed
    assert "gmail_get_profile" in names
    for a in GMAIL_ACTIONS:
        assert action_id_to_tool_name(a.action_id) in names


async def test_named_tool_carries_the_table_schema():
    adapter = FastMCP("t")
    await register_gateway_tools(
        adapter, GMAIL_PROFILE, guide_fetcher=AsyncMock(return_value=_EMPTY_GUIDE)
    )
    tool = await adapter.get_tool("gmail_send_email")
    assert tool.parameters == _BY_ID["gmail.send_email"].input_schema


async def test_handler_forwards_the_dotted_actionid():
    adapter = FastMCP("t")
    await register_gateway_tools(
        adapter, GMAIL_PROFILE, guide_fetcher=AsyncMock(return_value=_EMPTY_GUIDE)
    )
    captured = {}

    async def _fake(db, *, token, args):
        captured.update(args)
        return {"ok": True}

    class _CM:
        async def __aenter__(self):
            return "DB"

        async def __aexit__(self, *e):
            return False

    with (
        patch("src.adapter.warm_start.bearer_token", return_value="tok"),
        patch("src.adapter.warm_start.handle_execute_action", _fake),
        patch("src.adapter.warm_start.get_session_factory", lambda: lambda: _CM()),
    ):
        await (await adapter.get_tool("gmail_get_profile")).run({})
    assert captured["actionId"] == "gmail.get_profile"  # dotted actionId reaches the adapter


async def test_guide_fetch_failure_still_ships_table_schema():
    adapter = FastMCP("t")
    await register_gateway_tools(
        adapter, GMAIL_PROFILE, guide_fetcher=AsyncMock(side_effect=RuntimeError("down"))
    )
    tool = await adapter.get_tool("gmail_get_message")
    assert tool.parameters == _BY_ID["gmail.get_message"].input_schema


async def test_register_still_registers_when_guide_fetch_fails():
    adapter = FastMCP("test")
    fetcher = AsyncMock(side_effect=RuntimeError("OC down"))

    count = await register_gateway_tools(adapter, GMAIL_PROFILE, guide_fetcher=fetcher)

    assert count == len(GMAIL_PROFILE.action_allowlist)


async def test_register_warns_on_param_drift(caplog):
    adapter = FastMCP("test")
    drifted = _guide(
        "## Input Parameters\n\n"
        "| Name | Required | Type |\n| ---- | -------- | ---- |\n"
        "| `query` | No | `string` |\n| `newParam` | No | `string` |\n"
    )
    fetcher = AsyncMock(return_value=drifted)

    with caplog.at_level(logging.WARNING):
        await register_gateway_tools(adapter, GMAIL_PROFILE, guide_fetcher=fetcher)

    assert any("drift" in r.message for r in caplog.records)


async def test_register_opaque_fallback_for_action_without_hand_typed_schema():
    profile = GatewayProfile(
        provider_id="gmail",
        action_allowlist=frozenset({"gmail.unknown_action"}),
        action_required_capability={"gmail.unknown_action": "email.read"},
    )
    adapter = FastMCP("test")
    fetcher = AsyncMock(return_value=_guide(""))

    await register_gateway_tools(adapter, profile, guide_fetcher=fetcher)

    tool = await adapter.get_tool(action_id_to_tool_name("gmail.unknown_action"))
    assert tool.parameters == {"type": "object", "additionalProperties": True}


async def test_named_tool_handler_forwards_actionid_input_and_token():
    adapter = FastMCP("test")
    await register_gateway_tools(
        adapter, GMAIL_PROFILE, guide_fetcher=AsyncMock(return_value=_guide(""))
    )

    captured: dict = {}

    async def _fake_handle(db, *, token, args):
        captured["db"] = db
        captured["token"] = token
        captured["args"] = args
        return {"ok": True}

    class _FakeCM:
        async def __aenter__(self):
            return "FAKE_DB"

        async def __aexit__(self, *exc):
            return False

    with (
        patch("src.adapter.warm_start.bearer_token", return_value="tok-123"),
        patch("src.adapter.warm_start.handle_execute_action", _fake_handle),
        patch("src.adapter.warm_start.get_session_factory", lambda: lambda: _FakeCM()),
    ):
        tool = await adapter.get_tool("gmail_get_message")
        await tool.run({"messageId": "m1"})

    assert captured["db"] == "FAKE_DB"
    assert captured["token"] == "tok-123"
    assert captured["args"] == {"actionId": "gmail.get_message", "input": {"messageId": "m1"}}
