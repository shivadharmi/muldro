"""Step 6A.5 Task 8: end-to-end deep-path tool execution guard.

Proves the core 6A.5 guarantee with NO real API / network:

1. A Jarvis tool (ok_tool, err_tool) ACTUALLY EXECUTES through a compiled
   ``build_deep_agent`` graph via the REAL central dispatcher.
2. A deepagents built-in (write_todos) runs its OWN body (fall-through) and
   does NOT appear in the EXECUTED list.
3. The frozen ``blocked`` ← ``status == "error"`` SSE mapping holds.
4. Shell tripwires never fire (no ``error`` frame, ``agent_done`` present).

All streamed through the REAL ``stream_deep_agent_events`` adapter with a
scripted offline model + MemorySaver (zero Anthropic API calls).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.middleware.jarvis_tool_dispatcher import make_jarvis_tool_dispatcher
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.tool_bridge import build_tool_shells
from src.orchestrator.agents import SubAgent, ThinkingConfig

MODEL_ID = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Observability: track which Jarvis tools fake_execute_tool was called for.
# Declared at module level so the test can clear/inspect it easily.
# ---------------------------------------------------------------------------
EXECUTED: list[str] = []


async def fake_execute_tool(name: str, args: dict, user_id: str, workspace_id: str) -> dict:
    EXECUTED.append(name)
    if name == "ok_tool":
        return {"ok": 1}
    if name == "err_tool":
        return {"error": "nope", "blocked": True}
    return {"error": f"unexpected {name}"}


# ---------------------------------------------------------------------------
# Scripted offline model: turn 1 = 3 tool calls, turn 2 = final text.
# Mirrors _ScriptedModel from spikes/deep_stream/central_dispatcher_proof.py
# but extended to emit THREE concurrent tool calls (ok_tool, err_tool,
# write_todos) in turn 1.
# ---------------------------------------------------------------------------


def _token_text(chunk: AIMessageChunk) -> str:
    """Extract plain text from a chunk's content for the callback."""
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


class _ThreeToolScriptedModel(BaseChatModel):
    """Streams scripted AIMessageChunks — fully offline.

    Turn 1 (no ToolMessages yet): emits tool_call_chunks for ok_tool,
    err_tool, and write_todos in a single AIMessageChunk, then a usage chunk.
    Turn 2 (after ToolMessages arrive): emits final text.
    """

    @property
    def _llm_type(self) -> str:
        return "three-tool-scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ThreeToolScriptedModel":  # noqa: ANN401
        # deepagents calls bind_tools; we accept and ignore (offline model).
        return self

    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            # Single chunk emitting all three tool calls simultaneously.
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="ok_tool",
                        args=json.dumps({}),
                        id="call_ok",
                        index=0,
                    ),
                    tool_call_chunk(
                        name="err_tool",
                        args=json.dumps({}),
                        id="call_err",
                        index=1,
                    ),
                    tool_call_chunk(
                        name="write_todos",
                        args=json.dumps({"todos": [{"content": "task-A", "status": "pending"}]}),
                        id="call_todo",
                        index=2,
                    ),
                ],
            ),
            # Usage / stop-reason metadata chunk.
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": "All done.", "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 50,
                    "output_tokens": 5,
                    "total_tokens": 55,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        took_tool_turn = any(isinstance(m, ToolMessage) for m in messages)
        return self._turn2() if took_tool_turn else self._turn1()

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._script_for(messages):
            gen = ChatGenerationChunk(message=chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token(_token_text(chunk), chunk=gen)
            yield gen

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(
            content=merged.content,
            tool_calls=list(merged.tool_calls),
            usage_metadata=merged.usage_metadata,
            response_metadata=merged.response_metadata,
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("sync _generate not used in async tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_perceiver_agent() -> SubAgent:
    """Read-only test agent with capability_scope={'test.read'}."""
    return SubAgent(
        name="perceiver",
        prompt="You are a test perceiver.",
        model_tier="sonnet",
        capability_scope={"test.read"},
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _make_db_factory():
    """Async-context-manager factory yielding a dummy db object.

    Passed to build_deep_agent so the capability_scope middleware IS installed.
    The _is_in_scope function is patched separately, so this db is never
    actually queried against a real registry.
    """

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _jarvis_tool_defs() -> list[dict]:
    return [
        {
            "name": "ok_tool",
            "description": "A tool that succeeds.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "err_tool",
            "description": "A tool that returns an error.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


# ---------------------------------------------------------------------------
# The load-bearing end-to-end guard (Task 8)
# ---------------------------------------------------------------------------


async def test_deep_path_tool_execution_end_to_end() -> None:
    """Jarvis tools execute via the central dispatcher; write_todos runs its own body.

    Wiring:
    - capability_scope (OUTER, installed by build_deep_agent because db_factory is given)
      → _is_in_scope is stubbed to allow ok_tool / err_tool; write_todos exempt as built-in.
    - jarvis_tool_dispatcher (INNER, in extra_middleware)
      → intercepts ok_tool / err_tool, calls fake_execute_tool; falls through write_todos.
    - Shell bodies → tripwires that RAISE if ever invoked; they must never fire.
    - stream_deep_agent_events → the REAL SSE adapter.
    """
    # Reset the shared execute log before each run.
    EXECUTED.clear()

    agent_def = _make_perceiver_agent()
    shells = build_tool_shells(_jarvis_tool_defs())

    dispatcher = make_jarvis_tool_dispatcher(
        execute_tool=fake_execute_tool,
        user_id="u",
        workspace_id="ws",
    )

    # Stub (a) _is_in_scope so ok_tool / err_tool pass the scope check, and
    # (b) _has_write_capability_in_scope so the build-time fail-closed probe
    # doesn't fire (the agent is read-only; this skips the DB write-cap lookup).
    # The capability_scope_guard wrapper itself (including the built-in exemption
    # for write_todos) remains REAL.
    with (
        patch(
            "src.deep_runtime.middleware.capability_scope._is_in_scope",
            new=AsyncMock(side_effect=lambda name, *a, **k: name in {"ok_tool", "err_tool"}),
        ),
        patch(
            "src.deep_runtime.agent_builder._has_write_capability_in_scope",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "src.deep_runtime.agent_builder.build_chat_model",
            return_value=_ThreeToolScriptedModel(),
        ),
    ):
        agent = await build_deep_agent(
            agent_def,
            shells,
            workspace_id="ws",
            db_factory=_make_db_factory(),
            extra_middleware=(dispatcher,),
            checkpointer=MemorySaver(),
            system_prompt="test",
        )

        # Stream inside the patch context: capability_scope_guard calls _is_in_scope
        # as a global lookup at invocation time (not a captured closure variable),
        # so the stub must still be active when astream() processes tool calls.
        frames = [
            f
            async for f in stream_deep_agent_events(
                agent,
                {"messages": [{"role": "user", "content": "go"}]},
                {"configurable": {"thread_id": "t8"}},
                agent_name="perceiver",
                model=MODEL_ID,
            )
        ]

    # --- classify frames ---
    kinds = [f["event"] for f in frames]
    results: dict[str, dict] = {f["tool"]: f for f in frames if f["event"] == "tool_result"}

    # --- assertions ---

    # 1. ok_tool executed through execute_tool, no error → blocked=False.
    assert "ok_tool" in results, f"no tool_result for ok_tool; frames={frames}"
    assert results["ok_tool"]["blocked"] is False, (
        f"ok_tool should not be blocked; frame={results['ok_tool']}"
    )

    # 2. err_tool executed through execute_tool, returned error dict → blocked=True
    #    (frozen mapping: {"error": ..., "blocked": True} → ToolMessage(status="error")
    #    → blocked=True in the SSE frame).
    assert "err_tool" in results, f"no tool_result for err_tool; frames={frames}"
    assert results["err_tool"]["blocked"] is True, (
        f"err_tool should be blocked; frame={results['err_tool']}"
    )

    # 3. write_todos (deepagents built-in) produced a tool_result (ran its own body).
    assert "write_todos" in results, (
        f"write_todos built-in did not produce a tool_result; frames={frames}"
    )

    # 4. The dispatcher did NOT hijack write_todos (it fell through to the real handler).
    assert "write_todos" not in EXECUTED, (
        f"dispatcher should not have dispatched write_todos; EXECUTED={EXECUTED}"
    )

    # 5. Both Jarvis tools were dispatched through fake_execute_tool.
    assert {"ok_tool", "err_tool"} <= set(EXECUTED), (
        f"expected ok_tool and err_tool in EXECUTED; got {EXECUTED}"
    )

    # 6. No shell tripwire fired (shells never ran their bodies).
    #    A raising shell propagates out of astream → adapter catches it → emits
    #    a single error frame and stops (no agent_done). Both conditions must hold.
    assert "error" not in kinds, (
        f"an 'error' frame appeared — a shell tripwire may have fired; frames={frames}"
    )

    # 7. Stream completed cleanly (agent_done present).
    assert any(f["event"] == "agent_done" for f in frames), (
        f"no agent_done frame — stream did not complete; frames={frames}"
    )

    # 8. Balanced tool_call / tool_result counts (>= 3: ok_tool, err_tool, write_todos).
    n_calls = kinds.count("tool_call")
    n_results = kinds.count("tool_result")
    assert n_calls == n_results >= 3, (
        f"expected >= 3 balanced tool_call/tool_result pairs; "
        f"got calls={n_calls} results={n_results}; frames={frames}"
    )
