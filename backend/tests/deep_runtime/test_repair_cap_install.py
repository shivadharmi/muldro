"""R3a install seam: ``_build_deep_agent_for`` inserts the repair-cap middleware
immediately OUTER of ``muldro_tool_dispatcher`` — in both the dormant chain
``(write_lock, repair_cap, dispatcher)`` and the read-back chain
``(write_lock, read_back, repair_cap, dispatcher)``.

Offline, in the style of ``test_permission_gate_install.py``: ``build_deep_agent`` is
patched to an AsyncMock so nothing compiles, and each middleware factory is patched to
return a unique sentinel so POSITION is provable by identity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

INVOKER = "src.orchestrator.agent_invoker"
TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}


def _make_invoker(**settings_overrides) -> AgentInvoker:
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.execute_tool = MagicMock()

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", **settings_overrides),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={},
        checkpointer_provider=lambda: None,
    )


def _agent() -> SubAgent:
    return SubAgent(name="lead", prompt="p", model_tier="sonnet", capability_scope={"email.send"})


async def _capture(**settings_overrides):
    """Return ``(extra_middleware, sentinels_dict, make_repair_cap_mock)``."""
    inv = _make_invoker(**settings_overrides)
    sentinels = {
        "write_lock": object(),
        "read_back": object(),
        "repair_cap": object(),
        "dispatcher": object(),
    }

    with (
        patch(f"{INVOKER}.build_deep_agent", AsyncMock(return_value=object())) as bda,
        patch(f"{INVOKER}.make_write_lock_middleware", return_value=sentinels["write_lock"]),
        patch(f"{INVOKER}.make_readback_middleware", return_value=sentinels["read_back"]),
        patch(
            f"{INVOKER}.make_repair_cap_middleware", return_value=sentinels["repair_cap"]
        ) as make_cap,
        patch(f"{INVOKER}.make_muldro_tool_dispatcher", return_value=sentinels["dispatcher"]),
    ):
        await inv._build_deep_agent_for(
            _agent(),
            [TOOL_DEF],
            user_id="u",
            workspace_id="ws",
            thread_id="th",
            authorization_source="direct_user_request",
            system_prompt="sys",
            permission_mode=None,
            presence="absent",
        )
    return bda.call_args.kwargs["extra_middleware"], sentinels, make_cap


async def test_repair_cap_sits_between_write_lock_and_dispatcher():
    """Dormant read-back: the tail of the chain is (write_lock, repair_cap, dispatcher)."""
    mw, s, make_cap = await _capture(deep_readback_enabled=False)

    make_cap.assert_called_once()
    i = mw.index(s["write_lock"])
    assert mw[i + 1] is s["repair_cap"], "repair_cap must sit immediately after write_lock"
    assert mw[i + 2] is s["dispatcher"], "repair_cap must sit immediately before the dispatcher"
    assert s["read_back"] not in mw


async def test_repair_cap_sits_inner_of_readback_and_outer_of_dispatcher():
    """Read-back enabled: (write_lock, read_back, repair_cap, dispatcher)."""
    mw, s, make_cap = await _capture(deep_readback_enabled=True)

    make_cap.assert_called_once()
    i = mw.index(s["write_lock"])
    assert mw[i + 1] is s["read_back"]
    assert mw[i + 2] is s["repair_cap"]
    assert mw[i + 3] is s["dispatcher"]


async def test_repair_cap_is_always_installed_exactly_once():
    """Not flag-gated: one cap per turn on both branches, never two."""
    for readback in (False, True):
        mw, s, _ = await _capture(deep_readback_enabled=readback)
        assert [m for m in mw if m is s["repair_cap"]] == [s["repair_cap"]]
