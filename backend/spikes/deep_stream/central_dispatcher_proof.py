"""PROOF (Step 6A.5 research): a single wrap_tool_call middleware can be the universal
executor for Muldro tools.

Assertions:
1. A schema-shell StructuredTool whose coroutine RAISES is registered; the model calls it,
   but its body NEVER runs because the central middleware short-circuits (returns a
   ToolMessage without calling `handler`).
2. The fake `execute_tool` IS called with (name, args) recovered from request.tool_call.
3. The middleware's synthesized ToolMessage reaches the model (turn-2 final answer produced).
4. deepagents' OWN built-in tool `write_todos` is NOT hijacked: for a non-Muldro name the
   middleware falls through to `handler(request)` and the real built-in body runs (todos land
   in state).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

MODEL_ID = "claude-sonnet-4-6"

# --- observability counters -------------------------------------------------
SHELL_RAN = {"echo": 0}
EXECUTE_TOOL_CALLS: list[tuple[str, dict]] = []


# --- the schema-shell tool: body must NEVER run -----------------------------
async def _shell_body(**kwargs: Any) -> dict:
    SHELL_RAN["echo"] += 1
    raise AssertionError("SHELL BODY RAN — the middleware failed to short-circuit")


def _make_shell(name: str, schema: dict) -> StructuredTool:
    return StructuredTool(name=name, description=f"{name} (schema shell)",
                          args_schema=schema, coroutine=_shell_body)


# --- the fake Muldro dispatcher (stands in for ToolExecutor.execute_tool) ---
async def fake_execute_tool(name: str, tool_input: dict, user_id: str, workspace_id: str) -> dict:
    EXECUTE_TOOL_CALLS.append((name, tool_input))
    return {"ok": True, "echoed": tool_input}


# --- the ONE central-dispatcher middleware ----------------------------------
def make_central_dispatcher(muldro_names: set[str], *, user_id: str, workspace_id: str) -> AgentMiddleware:
    @wrap_tool_call
    async def central_dispatcher(request, handler):
        name = request.tool_call["name"]
        if name not in muldro_names:
            # deepagents built-in (write_todos, ls, ...) — run its real body.
            return await handler(request)
        args = request.tool_call["args"]
        result = await fake_execute_tool(name, args, user_id, workspace_id)
        blocked = bool(result.get("error") or result.get("blocked"))
        return ToolMessage(
            content=json.dumps(result),
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error" if blocked else "success",
        )

    return central_dispatcher


# --- scripted fake model: turn 1 calls echo (Muldro) AND write_todos (builtin) ---
class _ScriptedModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        # record what the model was bound with (proves the shell schema is advertised)
        globals()["BOUND_TOOL_NAMES"] = sorted(getattr(t, "name", "?") for t in tools)
        return self

    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(name="echo", args=json.dumps({"text": "hello"}), id="call_echo", index=0),
                    tool_call_chunk(name="write_todos",
                                    args=json.dumps({"todos": [{"content": "task-A", "status": "pending"}]}),
                                    id="call_todo", index=1),
                ],
            ),
            AIMessageChunk(
                content=[],
                usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                                "input_token_details": {"cache_read": 0, "cache_creation": 0}},
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": "All done.", "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55,
                                "input_token_details": {"cache_read": 0, "cache_creation": 0}},
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        took_tool_turn = any(isinstance(m, ToolMessage) for m in messages)
        return self._turn2() if took_tool_turn else self._turn1()

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:  # noqa: ANN001
        for ch in self._script_for(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(self, messages, stop=None, run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs) -> ChatResult:  # noqa: ANN001
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(content=merged.content, tool_calls=list(merged.tool_calls),
                        usage_metadata=merged.usage_metadata, response_metadata=merged.response_metadata)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *a: Any, **k: Any) -> ChatResult:
        raise NotImplementedError


async def main() -> None:
    echo_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    shell = _make_shell("echo", echo_schema)
    dispatcher = make_central_dispatcher({"echo"}, user_id="u", workspace_id="ws")

    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[shell],
        middleware=[dispatcher],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "proof-1"}}
    final = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, config)

    msgs = final["messages"]
    tool_msgs = {m.name: m for m in msgs if isinstance(m, ToolMessage)}
    todos = final.get("todos")
    final_ai = [m for m in msgs if isinstance(m, AIMessage) and not m.tool_calls]

    print("=== RESULTS ===")
    print("bound tool names (model saw):", globals().get("BOUND_TOOL_NAMES"))
    print("shell body ran count:", SHELL_RAN["echo"], "(must be 0)")
    print("execute_tool calls:", EXECUTE_TOOL_CALLS, "(must contain echo)")
    print("echo ToolMessage.status:", tool_msgs.get("echo") and tool_msgs["echo"].status)
    print("echo ToolMessage.content:", tool_msgs.get("echo") and tool_msgs["echo"].content)
    print("write_todos ToolMessage present:", "write_todos" in tool_msgs)
    print("write_todos ToolMessage.content:", tool_msgs.get("write_todos") and tool_msgs["write_todos"].content)
    print("state todos (builtin real body ran):", todos)
    print("final AI answer:", [m.text() if hasattr(m, "text") else m.content for m in final_ai])

    # --- assertions ---
    assert SHELL_RAN["echo"] == 0, "shell body ran — short-circuit FAILED"
    assert ("echo", {"text": "hello"}) in EXECUTE_TOOL_CALLS, "execute_tool not called for echo"
    assert "echo" in tool_msgs and tool_msgs["echo"].status == "success"
    assert todos and any(t.get("content") == "task-A" for t in todos), "builtin write_todos body did NOT run (fall-through broken)"
    assert final_ai, "model did not produce a final answer from the middleware result"
    print("\nALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
