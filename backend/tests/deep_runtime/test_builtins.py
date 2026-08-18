"""Step 6A.5: DEEPAGENTS_BUILTIN_NAMES must track the tools deepagents auto-installs.

This is a drift-guard test: it compiles a real minimal deep agent (no extra Muldro
tools, a fake offline model) and asserts that the constant equals the compiled
agent's built-in tool names.  A deepagents upgrade that changes those names will
cause this test to fail loudly instead of silently mis-gating tool calls through
the capability_scope middleware.

Introspection path (verified against deepagents 0.6.11):
    compiled_graph.nodes['tools'].bound.tools_by_name.keys()
where ``compiled_graph.nodes['tools'].bound`` is a LangGraph ``ToolNode`` that holds
all tools installed by the auto-middleware stack (FilesystemMiddleware + TodoList +
Subagent).
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class _OfflineFakeChatModel(BaseChatModel):
    """Minimal offline model — create_deep_agent compilation requires a BaseChatModel
    but no network call is made during graph construction or this test."""

    @property
    def _llm_type(self) -> str:
        return "offline-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_OfflineFakeChatModel":  # noqa: ANN401
        return self

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _compiled_builtin_tool_names() -> frozenset[str]:
    """Compile a real deepagents agent with NO extra Muldro tools and return the
    frozenset of tool names the framework auto-installed.

    Introspection path: compiled_graph.nodes['tools'].bound.tools_by_name.keys()
    """
    compiled = create_deep_agent(
        model=_OfflineFakeChatModel(),
        tools=[],  # no extra tools — only the deepagents built-ins
        system_prompt="drift-guard probe",
    )
    return frozenset(compiled.nodes["tools"].bound.tools_by_name.keys())


async def test_builtins_match_a_compiled_agent():
    """DRIFT GUARD — fail loudly if deepagents changes its auto-installed built-ins.

    If this test fails after a deepagents upgrade, update DEEPAGENTS_BUILTIN_NAMES
    to match the new set and audit capability_scope + muldro_tool_dispatcher to
    ensure every new built-in is still exempt from Muldro registry gating.
    """
    from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES

    actual = _compiled_builtin_tool_names()
    assert DEEPAGENTS_BUILTIN_NAMES == actual, (
        f"DEEPAGENTS_BUILTIN_NAMES is out of date.\n"
        f"  Constant : {sorted(DEEPAGENTS_BUILTIN_NAMES)}\n"
        f"  Compiled : {sorted(actual)}\n"
        f"  Missing from constant : {sorted(actual - DEEPAGENTS_BUILTIN_NAMES)}\n"
        f"  Extra in constant     : {sorted(DEEPAGENTS_BUILTIN_NAMES - actual)}\n"
        "Update src/deep_runtime/builtins.py to match the compiled set."
    )
