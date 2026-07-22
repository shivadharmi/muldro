"""Phase 0 de-risking spike — Deep Agents migration.

THROWAWAY exploratory code. Not imported by src/. Purpose: prove the two facts
the whole strangler-fig migration depends on, BEFORE committing to Phases 1-5.

Validation goals
----------------
G1. MODEL LAYER (decision: direct Anthropic API, not Bedrock)
    Can `ChatAnthropic` drive Opus 4.8 with *adaptive* thinking + effort the way
    Jarvis's hand-rolled `build_thinking_params` does (orchestrator/agent_loop.py)?
    If yes → we delete `build_thinking_params`. If no → fallback is a thin custom
    BaseChatModel wrapping the raw Anthropic client (small, isolated).

G2. MIDDLEWARE SURFACE
    Can LangChain middleware host Jarvis's two load-bearing per-call policies?
      - capability-scope enforcement (today: agent_loop._resolve_tool_scope_and_server),
        fail-closed, via @wrap_tool_call
      - per-call cost/budget capture (today: agent_loop budget.record_usage +
        per-tool TokenUsage), via @after_model
    A deep agent must be able to BLOCK an out-of-scope tool call before execution
    and to OBSERVE token usage per model call.

Run
---
    cd backend
    source .venv/bin/activate
    pip install -r spikes/deepagents_phase0/requirements.txt
    JARVIS_ANTHROPIC_API_KEY=<your-anthropic-key> python spikes/deepagents_phase0/spike.py

Expected: G1 prints a thinking summary with no 400; G2 prints that the
out-of-scope tool was BLOCKED and that >0 output tokens were observed.

CONFIRMED API FACTS (against installed: deepagents 0.6.11, langchain 1.3.10,
langgraph 1.2.6, langchain-anthropic 1.4.6, langchain-core 1.4.8)
----------------------------------------------------------------
G1: ChatAnthropic exposes first-class `thinking: dict` and `effort: Literal[...]`
    fields. Pass thinking={"type":"adaptive","display":"summarized"} + effort="high"
    and OMIT temperature (None is dropped from the request body at send time —
    chat_models.py:1426 `{k:v for ... if v is not None}`), exactly matching
    build_thinking_params for Opus 4.8.

G2: @wrap_tool_call(func) decorates `def f(request, handler)`; `request` is a
    ToolCallRequest dataclass with .tool_call (dict: name/args/id), .tool, .state,
    .runtime. Return a ToolMessage WITHOUT calling handler() to short-circuit (block).
    @after_model decorates `def f(state, runtime)`; read token usage from the last
    AIMessage's .usage_metadata. Both decorators return AgentMiddleware instances
    accepted by create_deep_agent(middleware=[...]).
"""

from __future__ import annotations

import os
import sys

OPUS = "claude-opus-4-8"  # direct Anthropic model id (CLAUDE.md)


def _require_key() -> str:
    key = os.environ.get("JARVIS_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set JARVIS_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY) to run the spike.")
    return key


# ── G1: model layer — Opus 4.8 adaptive thinking via ChatAnthropic ──────────────
def goal1_model_layer(api_key: str) -> bool:
    from langchain_anthropic import ChatAnthropic

    # The adaptive-thinking + effort surface. Jarvis's build_thinking_params sends
    #   thinking={"type":"adaptive","display":"summarized"}
    #   output_config={"effort": "high"}
    # and OMITS temperature for Opus 4.8. ChatAnthropic 1.4.6 has first-class
    # `thinking` and `effort` fields; leaving temperature unset (None) drops it from
    # the request body entirely.
    candidates = [
        # 1) PRIMARY: first-class thinking + effort fields, temperature omitted.
        dict(
            model=OPUS,
            api_key=api_key,
            max_tokens=4096,
            thinking={"type": "adaptive", "display": "summarized"},
            effort="high",
        ),
        # 2) EQUIVALENT: pass effort via output_config dict instead of the shorthand.
        dict(
            model=OPUS,
            api_key=api_key,
            max_tokens=4096,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "high"},
        ),
    ]
    prompt = "Think step by step, then answer: what is 17 * 23? Show your reasoning."
    for i, kwargs in enumerate(candidates, 1):
        try:
            llm = ChatAnthropic(**kwargs)
            resp = llm.invoke(prompt)
            # Adaptive thinking surfaces as a 'thinking'/'reasoning' content block
            # (list content) and/or in usage_metadata. Detect both.
            content = resp.content
            has_thinking_block = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") in ("thinking", "reasoning")
                for b in content
            )
            usage = getattr(resp, "usage_metadata", None) or {}
            out_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0
            print(
                f"[G1] form #{i} OK — content_type={type(content).__name__}, "
                f"thinking_block_present={has_thinking_block}, "
                f"output_tokens={out_tokens}, "
                f"resp_metadata_keys={list((getattr(resp, 'response_metadata', {}) or {}).keys())}"
            )
            return True
        except Exception as e:  # noqa: BLE001 — spike: log and try next form
            print(f"[G1] form #{i} FAILED: {type(e).__name__}: {str(e)[:400]}")
    print(
        "[G1] FAIL — neither adaptive-thinking form worked via ChatAnthropic. "
        "Fallback path: custom BaseChatModel wrapping raw Anthropic client."
    )
    return False


# ── G2: middleware surface — capability-scope block + token observe ─────────────
def goal2_middleware(api_key: str) -> bool:
    from deepagents import create_deep_agent
    from langchain.agents.middleware import after_model, wrap_tool_call
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool

    observed = {"output_tokens": 0, "blocked": []}
    AGENT_SCOPE = {"math.add"}  # pretend this agent may only do math.add

    # Map tool name -> capability (Jarvis does this via ToolRegistry.get_tool).
    # `multiply` is a benign, NON-destructive out-of-scope tool: the policy block
    # (not the model's own judgement) is what must stop it. Using a benign tool
    # avoids the model self-refusing a scary-sounding call, which would mean the
    # @wrap_tool_call interceptor never gets exercised.
    TOOL_CAPABILITY = {"add": "math.add", "multiply": "math.multiply"}

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @tool
    def multiply(a: int, b: int) -> int:
        """Multiply two integers."""
        # The tool LOOKS ordinary to the model on purpose — nothing in its
        # description hints it is out of scope. The @wrap_tool_call interceptor
        # (capability_scope) is the ONLY thing that must stop it from running.
        return a * b  # must never run — capability_scope blocks it pre-execution

    # @wrap_tool_call signature: f(request, handler). `request` is a ToolCallRequest
    # dataclass with .tool_call (dict: name/args/id). Returning a ToolMessage WITHOUT
    # calling handler() short-circuits execution (the tool body never runs).
    @wrap_tool_call
    def capability_scope(request, handler):
        name = request.tool_call.get("name")
        cap = TOOL_CAPABILITY.get(name)
        if cap not in AGENT_SCOPE:  # fail-closed: unknown/out-of-scope -> block
            observed["blocked"].append(name)
            return ToolMessage(
                content=f"BLOCKED: '{name}' (capability {cap!r}) outside agent scope.",
                tool_call_id=request.tool_call.get("id", "blocked"),
                name=name,
            )
        return handler(request)

    # @after_model signature: f(state, runtime). Read token usage from the last
    # AIMessage's usage_metadata.
    @after_model
    def budget_observe(state, runtime):
        for msg in reversed(state.get("messages", [])):
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                observed["output_tokens"] += usage.get("output_tokens", 0)
                break
        return None

    try:
        agent = create_deep_agent(
            model=_make_model(api_key),
            tools=[add, multiply],
            system_prompt=(
                "You are a calculator agent. You MUST use the provided tools for "
                "every arithmetic operation — never compute in your head. Use `add` "
                "for addition and `multiply` for multiplication."
            ),
            middleware=[capability_scope, budget_observe],
        )
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Use your tools: compute 2 + 3, and also compute 4 * 5.",
                    }
                ]
            }
        )
        blocked_ok = "multiply" in observed["blocked"]
        tokens_ok = observed["output_tokens"] > 0
        print(
            f"[G2] blocked_out_of_scope={blocked_ok} "
            f"(blocked={observed['blocked']}), output_tokens={observed['output_tokens']}"
        )
        print(f"[G2] final: {str(result.get('messages', [])[-1])[:200]}")
        return blocked_ok and tokens_ok
    except Exception as e:  # noqa: BLE001
        import traceback

        print(f"[G2] FAILED: {type(e).__name__}: {str(e)[:500]}")
        traceback.print_exc()
        print("[G2] -> iterate on the middleware decorator signatures above.")
        return False


def _make_model(api_key: str):
    """Return a ChatAnthropic deepagents accepts. Use a small max_tokens / low effort
    so the G2 tool-using turn is cheap; thinking off to keep tool-calls deterministic."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=OPUS, api_key=api_key, max_tokens=2048)


if __name__ == "__main__":
    key = _require_key()
    print("=== Phase 0 spike: Deep Agents migration de-risk ===")
    g1 = goal1_model_layer(key)
    g2 = goal2_middleware(key)
    print("\n=== RESULT ===")
    print(
        f"G1 model-layer (Opus 4.8 adaptive thinking via ChatAnthropic): "
        f"{'PASS' if g1 else 'FAIL -> custom wrapper'}"
    )
    print(
        f"G2 middleware surface (scope block + token observe):           "
        f"{'PASS' if g2 else 'FAIL -> iterate'}"
    )
    sys.exit(0 if (g1 and g2) else 1)
