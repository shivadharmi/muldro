"""Muldro agents are not offered deepagents' virtual filesystem.

`create_deep_agent` auto-installs a scaffolding toolset Muldro cannot drop —
`FilesystemMiddleware` and `SubAgentMiddleware` are in deepagents' `_REQUIRED_MIDDLEWARE`
and `_apply_excluded_middleware` raises rather than strip them. So every agent was offered
`ls / read_file / write_file / edit_file / glob / grep / execute` regardless of its
capability scope. Measured: a plain "Hey!" builds a lead with an EMPTY Muldro scope and
still nine builtins; observed trials answered by calling `ls`, and by narrating the `task`
tool's own documentation.

The filesystem is deepagents' per-thread virtual state, so it is contained rather than a
sandbox escape. It is still an affordance that silently discards data: Muldro has no
filesystem feature, so a model that "saves" something there reports success over a write
that vanishes at end of thread. That is soul law 5 (continuity) broken by a tool we never
meant to offer, and it is the mechanism behind the observed memory loss.

`task` and `write_todos` stay. `task` is load-bearing — it is how the lead reaches a
delegate subagent, and `governor_delegate_critique` gates it by name. `write_todos` is an
internal planning scratchpad with nothing to lose.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.orchestrator.agents import SubAgent


class _RecordingFakeChatModel(BaseChatModel):
    """Records the tool names it was actually offered, then answers."""

    offered: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_RecordingFakeChatModel":  # noqa: ANN401
        for t in tools:
            name = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
            if isinstance(name, str):
                type(self).offered.append(name)
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def _read_only_lead() -> SubAgent:
    return SubAgent(name="lead", prompt="probe", model_tier="balanced", capability_scope=set())


async def _offered_tool_names(agent: SubAgent) -> set[str]:
    _RecordingFakeChatModel.offered = []
    model = _RecordingFakeChatModel()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.deep_runtime.agent_builder.build_chat_model",
            lambda *a, **k: _async_return(model),
        )
        compiled = await build_deep_agent(agent, tools=[], workspace_id="ws_test")
    await compiled.ainvoke({"messages": [("user", "hello")]})
    return set(_RecordingFakeChatModel.offered)


async def _async_return(value):
    return value


FILESYSTEM_BUILTINS = {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}


async def test_a_scopeless_lead_is_offered_no_filesystem():
    offered = await _offered_tool_names(_read_only_lead())

    assert offered, "the probe recorded nothing — bind_tools was not reached"
    assert not (offered & FILESYSTEM_BUILTINS), (
        f"filesystem tools offered to a lead with an empty capability scope: "
        f"{sorted(offered & FILESYSTEM_BUILTINS)}"
    )


async def test_delegation_and_planning_scaffolds_survive():
    """`task` is how a lead reaches a delegate and is gated by name elsewhere; removing it
    would break delegation silently."""
    offered = await _offered_tool_names(_read_only_lead())

    assert "task" in offered
    assert "write_todos" in offered


def test_the_suppressed_set_is_a_real_subset_of_the_builtins():
    """Drift guard: every name we suppress must be one deepagents actually installs, or the
    suppression is silently doing nothing."""
    from src.deep_runtime.builtins import VIRTUAL_FILESYSTEM_TOOL_NAMES

    assert VIRTUAL_FILESYSTEM_TOOL_NAMES == FILESYSTEM_BUILTINS
    assert VIRTUAL_FILESYSTEM_TOOL_NAMES < DEEPAGENTS_BUILTIN_NAMES
