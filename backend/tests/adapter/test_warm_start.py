import logging
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from src.adapter.enforcement import GMAIL_PROFILE, GatewayProfile
from src.adapter.warm_start import (
    GMAIL_ACTION_SCHEMAS,
    _param_names_from_guide,
    register_gateway_tools,
)


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


def test_every_allowlisted_action_has_a_hand_typed_schema():
    assert set(GMAIL_PROFILE.action_allowlist) <= set(GMAIL_ACTION_SCHEMAS)


def test_send_email_schema_models_cc_as_string_or_array():
    cc = GMAIL_ACTION_SCHEMAS["gmail.send_email"]["properties"]["cc"]
    assert cc == {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        "description": "Cc recipients.",
    }


def test_search_threads_marks_query_required():
    assert GMAIL_ACTION_SCHEMAS["gmail.search_threads"].get("required") == ["query"]


def test_param_names_from_guide_extracts_the_table_column():
    names = _param_names_from_guide(_guide(_FETCH_EMAILS_MD))
    assert names == {"query", "labelIds", "includeSpamTrash", "detail", "maxResults", "pageToken"}


def test_param_names_from_guide_returns_empty_when_no_markdown():
    assert _param_names_from_guide({"nope": 1}) == set()


async def test_register_serves_hand_typed_schemas_not_the_live_guide():
    adapter = FastMCP("test")
    fetcher = AsyncMock(return_value=_guide(_FETCH_EMAILS_MD))

    count = await register_gateway_tools(adapter, GMAIL_PROFILE, guide_fetcher=fetcher)

    tools = {t.name: t for t in await adapter.list_tools()}
    assert count == len(GMAIL_PROFILE.action_allowlist)
    for action_id in GMAIL_PROFILE.action_allowlist:
        assert tools[action_id].parameters == GMAIL_ACTION_SCHEMAS[action_id]


async def test_register_still_registers_when_guide_fetch_fails():
    adapter = FastMCP("test")
    fetcher = AsyncMock(side_effect=RuntimeError("OC down"))

    count = await register_gateway_tools(adapter, GMAIL_PROFILE, guide_fetcher=fetcher)

    tools = {t.name: t for t in await adapter.list_tools()}
    assert count == len(GMAIL_PROFILE.action_allowlist)
    assert tools["gmail.get_message"].parameters == GMAIL_ACTION_SCHEMAS["gmail.get_message"]


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

    tool = await adapter.get_tool("gmail.unknown_action")
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
        tool = await adapter.get_tool("gmail.get_message")
        await tool.run({"messageId": "m1"})

    assert captured["db"] == "FAKE_DB"
    assert captured["token"] == "tok-123"
    assert captured["args"] == {"actionId": "gmail.get_message", "input": {"messageId": "m1"}}
