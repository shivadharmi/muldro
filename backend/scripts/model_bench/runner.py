"""Run one benchmark task against one candidate model, through the REAL agent build.

What is real here: the tool schemas (resolved from the live registry for the task's
capability scope), the composed system prompt (`MULDRO_SOUL_CORE` + `LEAD_PROMPT`, or
`PLANNER_PROMPT_V2` with the live capability summary), the deep-agent graph, the
capability_scope guard, the virtual-filesystem suppression, and the tool dispatcher.

What is stubbed, deliberately: `execute_tool`. Every tool call returns a fixed result, so
runs are deterministic, no external service is touched, and an EMPTY inbox makes
fabrication detectable. The write gates (trust_gate / permission_gate) are NOT installed:
they decide whether a call EXECUTES, not whether a model CHOOSES to call, and tool calling
through the real gate chain is already established. Say so rather than implying otherwise.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.prompt_bridge import build_system_message, strip_cache_control
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import AGENTS, SubAgent, ThinkingConfig
from src.orchestrator.prompts import LEAD_PROMPT

from .tasks import BenchTask, TurnRecord

_DEFAULT_STUB = {"ok": True, "result": "no data", "note": "benchmark stub"}


def _stub_for(task: BenchTask, name: str) -> Any:
    if name in task.stubs:
        return task.stubs[name]
    for key, value in task.stubs.items():
        if key.startswith("__prefix__") and name.startswith(key.removeprefix("__prefix__")):
            return value
    return _DEFAULT_STUB


async def _resolve_scope(task: BenchTask, workspace_id: str) -> set[str]:
    """`None` means the full perceive read scope — the widest a chat lead ever holds."""
    if task.scope is not None:
        return set(task.scope)
    perceiver = AGENTS.get("perceiver")
    return set(perceiver.capability_scope) if perceiver else set()


def _bench_agent(name: str, prompt: str, scope: set[str]) -> SubAgent:
    return SubAgent(
        name=name,
        prompt=prompt,
        model_tier="balanced",
        capability_scope=scope,
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


async def run_task(
    task: BenchTask,
    model,
    *,
    tool_executor,
    db_factory,
    workspace_id: str,
    user_id: str,
    supports_prompt_cache: bool,
) -> TurnRecord:
    scope = await _resolve_scope(task, workspace_id)

    if task.planner:
        from src.orchestrator.capability_summary import generate_capability_summary

        async with db_factory() as db:
            summary = await generate_capability_summary(db, workspace_id)
        agent = _bench_agent("planner", AGENTS["planner"].prompt, scope)
        blocks = AgentInvoker.build_system_prompt(
            SimpleNamespace(), agent, capability_summary=summary
        )
    else:
        agent = _bench_agent("lead", LEAD_PROMPT, scope)
        blocks = AgentInvoker.build_system_prompt(SimpleNamespace(), agent)

    system_prompt = build_system_message(blocks)
    if not supports_prompt_cache:
        system_prompt = strip_cache_control(system_prompt)

    tools = await tool_executor.get_tools_for_agent(agent, workspace_id=workspace_id)
    calls: list[tuple[str, dict]] = []

    async def _execute_tool(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        calls.append((name, dict(args or {})))
        return _stub_for(task, name)

    from src.deep_runtime.tool_bridge import build_tool_shells

    dispatcher = make_muldro_tool_dispatcher(
        execute_tool=_execute_tool, user_id=user_id, workspace_id=workspace_id
    )

    async def _model(*_a: Any, **_k: Any):
        return model

    import src.deep_runtime.agent_builder as ab

    original = ab.build_chat_model
    ab.build_chat_model = _model
    try:
        compiled = await build_deep_agent(
            agent,
            tools=build_tool_shells(tools),
            workspace_id=workspace_id,
            db_factory=db_factory,
            extra_middleware=[dispatcher],
            system_prompt=system_prompt,
        )
        started = time.monotonic()
        error = None
        reply = ""
        try:
            out = await compiled.ainvoke({"messages": [("user", task.message)]})
            for msg in reversed(out.get("messages", [])):
                if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
                    reply = msg.content
                    break
                if isinstance(msg, AIMessage) and isinstance(msg.content, list):
                    text = "".join(b.get("text", "") for b in msg.content if isinstance(b, dict))
                    if text:
                        reply = text
                        break
        except Exception as exc:  # noqa: BLE001 — a candidate that crashes the loop is data
            error = f"{type(exc).__name__}: {exc}"
        latency = int((time.monotonic() - started) * 1000)
    finally:
        ab.build_chat_model = original

    return TurnRecord(
        tools_bound=[t["name"] for t in tools],
        tool_calls=calls,
        reply=reply,
        error=error,
        latency_ms=latency,
    )
