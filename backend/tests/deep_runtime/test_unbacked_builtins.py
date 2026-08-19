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

`write_todos` stays — an internal planning scratchpad with nothing to lose.

`task` stays only when a Muldro delegate is actually registered. With `subagents=()` —
the live default, since `deep_delegates_enabled` is False — deepagents auto-adds its OWN
`general-purpose` subagent, hands it `"tools": _tools or []` (Muldro's inert shells) and a
middleware list containing NONE of Muldro's: no capability_scope, no dispatcher, no gates,
and not this suppressor either. So `task` there advertises an agent Muldro never meant to
exist, holding shells whose bodies are tripwire AssertionErrors, with its own unfiltered
filesystem. Its tool description is several hundred words about that agent — which is what
the trial that "narrated the task tool's documentation" was reading from.
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


async def _offered_tool_names(agent: SubAgent, subagents=()) -> set[str]:
    _RecordingFakeChatModel.offered = []
    model = _RecordingFakeChatModel()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.deep_runtime.agent_builder.build_chat_model",
            lambda *a, **k: _async_return(model),
        )
        compiled = await build_deep_agent(
            agent, tools=[], workspace_id="ws_test", subagents=subagents
        )
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


async def test_the_planning_scratchpad_survives():
    offered = await _offered_tool_names(_read_only_lead())

    assert "write_todos" in offered


async def test_task_is_not_offered_when_no_delegate_is_registered():
    """With `subagents=()` — the live default — `task` reaches deepagents' OWN
    general-purpose subagent, not a Muldro delegate. Offering it advertises an agent that
    holds inert Muldro shells and an unfiltered filesystem, and can do nothing else."""
    offered = await _offered_tool_names(_read_only_lead(), subagents=())

    assert "task" not in offered


async def test_task_survives_when_a_delegate_is_registered():
    """Teeth: the suppression must be conditional. With a delegate wired, `task` is the
    only route to it and `governor_delegate_critique` gates it by name."""
    delegate = {
        "name": "perceiver",
        "description": "read-only research delegate",
        "system_prompt": "probe",
        "model": _RecordingFakeChatModel(),
        "tools": [],
    }
    offered = await _offered_tool_names(_read_only_lead(), subagents=[delegate])

    assert "task" in offered


def test_the_suppressed_set_is_a_real_subset_of_the_builtins():
    """Drift guard: every name we suppress must be one deepagents actually installs, or the
    suppression is silently doing nothing."""
    from src.deep_runtime.builtins import DELEGATION_TOOL_NAME, VIRTUAL_FILESYSTEM_TOOL_NAMES

    assert VIRTUAL_FILESYSTEM_TOOL_NAMES == FILESYSTEM_BUILTINS
    assert VIRTUAL_FILESYSTEM_TOOL_NAMES < DEEPAGENTS_BUILTIN_NAMES
    assert DELEGATION_TOOL_NAME in DEEPAGENTS_BUILTIN_NAMES
