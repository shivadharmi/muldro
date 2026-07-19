"""Unified agent loop — single implementation for streaming and non-streaming.

Extracts the duplicated logic from JarvisOrchestrator._call_agent() and
_call_agent_stream() into a single async generator that yields typed events.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.contracts import SpanToolCall
from src.integrations.provider_map import provider_for_server
from src.orchestrator.hooks import _sanitize_secrets, audit_post_tool_hook, governor_pre_tool_hook
from src.services.execution_support import CancellationRequested, _check_cancellation
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Terminal steer appended to a tool_result when its MCP server is unavailable
# (auth_required / bridge-not-initialized). Tells the model to stop retrying
# that integration this turn and report back instead of burning more rounds.
_UNAVAILABLE_STEER = (
    " This integration needs re-authorization and cannot be used in this session. "
    "Do not retry its tools; tell the user to reconnect it."
)
_BRIDGE_NOT_INIT_STEER = (
    " This integration is unavailable in this session. "
    "Do not retry its tools; tell the user to reconnect it."
)


def _unavailable_provider(result: Any) -> str | None:
    """Return the provider key to mark unavailable, or None.

    Detects the two "server is down for auth/unavailable reasons" shapes the
    tool path can return — the structured ``auth_required`` envelope (carrying
    ``provider``/``server``) and the legacy ``"MCP bridge not initialized"``
    error. Resolution is pure string work (``provider_for_server`` is a
    substring matcher) — no DB or I/O on the loop's hot path.
    """
    if not isinstance(result, dict):
        return None
    if result.get("error_code") == "auth_required":
        provider = result.get("provider")
        if provider:
            return str(provider)
        server = result.get("server")
        if server:
            return provider_for_server(str(server))
    return None


def _unavailable_server(result: Any) -> str | None:
    """Return the MCP server name to mark unavailable, or None.

    Pulled from the structured ``auth_required`` envelope's ``server`` field.
    Tracking by the registered SERVER name (rather than only the provider
    inferred from the tool NAME) is the C5 fix: a Google tool like
    ``search_messages`` has no provider substring, so the provider-name
    short-circuit never matched it. The envelope always carries the real server,
    so keying the short-circuit on the server makes it robust to tool names that
    don't embed their provider.
    """
    if not isinstance(result, dict):
        return None
    if result.get("error_code") == "auth_required":
        server = result.get("server")
        if server:
            return str(server)
    return None


# Max chars persisted per span field after serialization. Live tool results are
# unaffected — this only caps/redacts what gets written into trace spans.
_MAX_SPAN_FIELD_CHARS = 20_000

# Keys whose values are redacted wholesale during structure-preserving sanitization.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|authorization|access_token"
    r"|refresh_token|client_secret)",
    re.IGNORECASE,
)


def _sanitize_for_span(value: Any) -> Any:
    """Redact secrets and truncate large values before persisting to a trace span.

    Structure-preserving: dicts/lists keep their shape so trace replay stays
    useful; secret-like keys are redacted wholesale and string values are
    pattern-scrubbed. Does NOT alter the live result returned to the agent loop
    — only the copy written into SpanToolCall.input_data / output_data.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = "***REDACTED***"
            else:
                out[k] = _sanitize_for_span(v)
        return out
    if isinstance(value, list):
        return [_sanitize_for_span(v) for v in value]
    if isinstance(value, str):
        redacted = _sanitize_secrets(value)
        if len(redacted) > _MAX_SPAN_FIELD_CHARS:
            redacted = redacted[:_MAX_SPAN_FIELD_CHARS] + "...[truncated]"
        return redacted
    return value


async def _resolve_tool_scope_and_server(
    tool_name: str,
    agent,  # SubAgent
    db_factory,
    workspace_id: str,
) -> tuple[bool, str | None]:
    """Resolve a tool's (in_scope, server_name) in ONE registry lookup.

    Combines capability-scope enforcement with server resolution so the loop's
    hot path issues a single ``get_tool`` per tool instead of two. Returns:

    - ``in_scope``: True only if the tool's registry capability is present in the
      agent's ``capability_scope`` (fail-closed for capability=None / unresolved
      / no-registry — mirrors ``_get_tools_for_agent``, which never offers such
      tools);
    - ``server``: the tool's registered MCP server name (or None) — used to key
      the per-turn unavailable-server short-circuit (C5). A name like
      ``search_messages`` carries no provider substring, so resolving its server
      from the registry is the only reliable way to short-circuit it.
    """
    scope = getattr(agent, "capability_scope", None)
    # Agents with no scope get no tools offered; nothing legitimate to allow.
    if not scope:
        return False, None
    if db_factory is None:
        return False, None
    async with db_factory() as db:
        registry = ToolRegistry(db, workspace_id=workspace_id or None)
        tool = await registry.get_tool(tool_name)
    if tool is None:
        return False, None
    server = getattr(tool, "server", None)
    capability = getattr(tool, "capability", None)
    if not capability:
        return False, server
    return capability in scope, server


async def _capability_in_scope(
    tool_name: str,
    agent,  # SubAgent
    db_factory,
    workspace_id: str,
) -> bool:
    """Backward-compatible capability-scope check (delegates to the combined
    resolver). Retained for callers/tests that only need the in-scope verdict."""
    in_scope, _server = await _resolve_tool_scope_and_server(
        tool_name, agent, db_factory, workspace_id
    )
    return in_scope


# ── Loop Event Types (internal, never serialized to SSE directly) ──


@dataclass
class LoopAgentStart:
    agent: str
    model: str


@dataclass
class LoopThinking:
    agent: str
    text: str
    is_thinking: bool = True  # True = extended thinking, False = reasoning text


@dataclass
class LoopTextDelta:
    agent: str
    text: str


@dataclass
class LoopToolCall:
    agent: str
    tool_name: str
    tool_input: dict


@dataclass
class LoopToolResult:
    agent: str
    tool_name: str
    result: Any
    blocked: bool = False
    latency_ms: int = 0


@dataclass
class LoopDone:
    agent: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    tools_called: list[str] = field(default_factory=list)
    tool_call_details: list[SpanToolCall] = field(default_factory=list)
    thinking_summary: str | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0


@dataclass
class LoopError:
    agent: str
    message: str


LoopEvent = (
    LoopAgentStart
    | LoopThinking
    | LoopTextDelta
    | LoopToolCall
    | LoopToolResult
    | LoopDone
    | LoopError
)


# CancellationRequested + _check_cancellation re-homed to services.execution_support
# (Step 11 Phase 4) and re-imported above; used at the cancel checkpoint + catch below.

_MAX_API_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds


async def _api_call_with_retry(client, api_kwargs: dict, agent_name: str):
    """Call Claude API with exponential backoff retry on rate limits."""
    for attempt in range(_MAX_API_RETRIES):
        try:
            return await client.messages.create(**api_kwargs)
        except anthropic.RateLimitError:
            if attempt < _MAX_API_RETRIES - 1:
                wait = min(_RETRY_BASE_DELAY * (2**attempt), 30)
                logger.warning(
                    "Rate limited on %s (attempt %d/%d), retrying in %.1fs",
                    agent_name,
                    attempt + 1,
                    _MAX_API_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                raise

    # Unreachable in practice (the loop either returns or re-raises), but make the
    # contract explicit so callers never silently receive None on a misconfigured
    # _MAX_API_RETRIES (e.g. 0).
    raise RuntimeError(f"API retry loop exhausted for {agent_name} without a response")


def _sanitize_content_blocks(content) -> list[dict]:
    """Convert SDK response content blocks to plain dicts for re-submission.

    Strips None-valued fields that the API rejects on input.
    """
    return [
        block.model_dump(exclude_none=True)
        if hasattr(block, "model_dump")
        else {"type": block.type}
        for block in content
    ]


def _is_thinking_error(err: Exception) -> bool:
    """Check if an API error is specifically about thinking block incompatibility."""
    msg = str(err).lower()
    return "thinking" in msg and (
        "disabled" in msg or "not supported" in msg or "cannot contain" in msg
    )


# Models whose API rejects `temperature`/`top_p`/`top_k` and the legacy
# `thinking:{type:"enabled", budget_tokens}` shape — they require adaptive
# thinking + output_config.effort (Opus 4.7/4.8, Fable 5, Mythos 5). Matched as
# substrings so Bedrock inference-profile IDs (us.anthropic.claude-opus-4-8)
# are covered too.
# MAINTENANCE: every new adaptive-only model (e.g. a future Opus 4.9) MUST be
# added here. A model not listed falls through to the legacy temperature+enabled
# path and 400s on every call (the exact outage this list was added to fix).
_ADAPTIVE_THINKING_MARKERS = (
    "opus-4-8",
    "opus-4-7",
    "fable-5",
    "mythos-5",
    "mythos-preview",
)


def _requires_adaptive_thinking(model: str) -> bool:
    """True when *model* rejects temperature + enabled-thinking (adaptive only)."""
    m = (model or "").lower()
    return any(marker in m for marker in _ADAPTIVE_THINKING_MARKERS)


def _effort_for_budget(budget_tokens: int | None) -> str:
    """Map a legacy per-agent thinking budget to an effort tier.

    Preserves the relative intent (Planner=8192 thinks hardest) without sending
    the now-rejected token budget. Default high — the recommended floor for
    intelligence-sensitive work on Opus 4.7/4.8."""
    if not budget_tokens or budget_tokens >= 8192:
        return "high"
    if budget_tokens >= 4096:
        return "medium"
    return "low"


def build_thinking_params(
    model: str,
    *,
    thinking_enabled: bool,
    budget_tokens: int | None,
    temperature: float,
) -> dict:
    """Return model-aware thinking/sampling kwargs for ``messages.create``.

    Adaptive-only models (Opus 4.7/4.8, Fable/Mythos 5) reject ``temperature``
    and ``thinking:{type:"enabled"}`` — use ``thinking:{type:"adaptive"}`` +
    ``output_config.effort`` and omit sampling params entirely. Legacy models
    keep the enabled-thinking + temperature surface they still accept."""
    if _requires_adaptive_thinking(model):
        if thinking_enabled:
            return {
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": _effort_for_budget(budget_tokens)},
            }
        # No thinking and no sampling params — both 400 on these models.
        return {}
    if thinking_enabled:
        return {
            "temperature": 1,  # required when enabled-thinking is on
            "thinking": {"type": "enabled", "budget_tokens": budget_tokens},
        }
    return {"temperature": temperature}


def _disable_thinking_in_kwargs(api_kwargs: dict, model: str, agent_temperature: float) -> None:
    """Disable thinking mid-loop after a thinking-incompatibility error.

    Drops the thinking block (and its paired effort), strips thinking blocks
    from history, and restores ``temperature`` only on models that accept it —
    adaptive-only models 400 on any sampling param."""
    api_kwargs.pop("thinking", None)
    api_kwargs.pop("output_config", None)
    if _requires_adaptive_thinking(model):
        api_kwargs.pop("temperature", None)
    else:
        api_kwargs["temperature"] = agent_temperature
    _strip_thinking_from_messages(api_kwargs.get("messages", []))


def _strip_thinking_from_messages(messages: list[dict]) -> None:
    """Remove thinking blocks from assistant messages in-place.

    Required when disabling thinking mid-loop: the API rejects messages
    containing thinking blocks when thinking is disabled.
    """
    for msg in messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            msg["content"] = [b for b in msg["content"] if b.get("type") != "thinking"]


async def agent_loop(
    *,
    client,
    agent,  # SubAgent
    model: str,
    system_blocks: list[dict],
    tools: list[dict],
    message: str,
    user_id: str,
    workspace_id: str,
    db_factory,
    services,
    budget,  # BudgetTracker
    trace,  # JarvisTrace | None
    execute_tool_fn,
    max_tool_rounds: int = 10,
    stream: bool = False,
    circuit_breaker=None,  # AnthropicCircuitBreaker | None
    run_id: str | None = None,  # B1: link tool-level approvals to execution context
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[LoopEvent, None]:
    """Core agent loop — yields LoopEvent instances.

    When stream=True, uses messages.stream() for token-by-token output.
    When stream=False, uses messages.create() (still yields same events).
    """
    agent_name = agent.name
    span = trace.start_span(agent_name, model=model) if trace else None

    yield LoopAgentStart(agent=agent_name, model=model)

    messages: list[dict] = [{"role": "user", "content": message}]
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    tools_called: list[str] = []
    tool_call_details: list[SpanToolCall] = []
    thinking_chunks: list[str] = []
    text = ""
    start_time = time.time()

    # Per-invocation circuit breaker for MCP servers that returned an
    # auth/unavailable error this turn. Resolved by OAuth provider so all of a
    # provider's tools (e.g. every gmail/google-workspace tool) are skipped
    # together. Reset every call — NOT module-global — so a later run is never
    # poisoned by an earlier one. Analogous to AnthropicCircuitBreaker, but
    # loop-scoped and per-provider.
    unavailable_providers: set[str] = set()
    # Companion set keyed by the MCP SERVER name (from the auth_required
    # envelope's `server` field). The primary short-circuit key (C5): unlike the
    # provider-from-tool-name heuristic, the server resolves reliably from the
    # registry even for tool names that embed no provider substring
    # (e.g. `search_messages` on google-workspace). Per-call, reset every loop.
    unavailable_servers: set[str] = set()

    # Thinking config from agent
    thinking_config = getattr(agent, "thinking", None)
    thinking_enabled = thinking_config.enabled if thinking_config else True
    thinking_budget_tokens = thinking_config.budget_tokens if thinking_config else None

    if thinking_enabled:
        if thinking_budget_tokens is None:
            thinking_budget_tokens = min(8192, max(1024, agent.max_tokens // 2))
        if thinking_budget_tokens >= agent.max_tokens:
            thinking_budget_tokens = agent.max_tokens - 1

    try:
        # Circuit breaker check — if API is in outage, fail fast
        if circuit_breaker and not circuit_breaker.is_available(model):
            text = f"[Agent {agent_name} skipped — API circuit open for {model}]"
            yield LoopError(agent=agent_name, message=text)
            yield LoopDone(agent=agent_name, text=text)
            return

        for _round in range(max_tool_rounds):
            _check_cancellation(cancel_event)

            api_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": agent.max_tokens,
                "system": system_blocks,
                "messages": messages,
            }

            api_kwargs.update(
                build_thinking_params(
                    model,
                    thinking_enabled=thinking_enabled,
                    budget_tokens=thinking_budget_tokens,
                    temperature=agent.temperature,
                )
            )

            if tools:
                api_kwargs["tools"] = tools

            response = None
            _api_start = time.time()

            if stream:
                try:
                    async with client.messages.stream(**api_kwargs) as api_stream:
                        async for event in api_stream:
                            if event.type == "content_block_delta":
                                delta = event.delta
                                if delta.type == "thinking_delta":
                                    thinking_chunks.append(delta.thinking)
                                    yield LoopThinking(
                                        agent=agent_name, text=delta.thinking, is_thinking=True
                                    )
                                elif delta.type == "text_delta":
                                    yield LoopTextDelta(agent=agent_name, text=delta.text)
                        response = await api_stream.get_final_message()
                except Exception as stream_err:
                    if response is None:
                        logger.warning(
                            "Streaming failed for %s, falling back: %s",
                            agent_name,
                            stream_err,
                        )
                        # Only disable thinking if the error is specifically about
                        # thinking blocks — not for transient network/Bedrock errors.
                        if thinking_enabled and _is_thinking_error(stream_err):
                            _disable_thinking_in_kwargs(api_kwargs, model, agent.temperature)
                            thinking_enabled = False
                        try:
                            response = await _api_call_with_retry(client, api_kwargs, agent_name)
                        except Exception as fallback_err:
                            # If fallback also fails due to thinking contamination,
                            # strip thinking and retry once more.
                            if thinking_enabled and _is_thinking_error(fallback_err):
                                logger.warning(
                                    "Fallback failed for %s due to thinking, retrying: %s",
                                    agent_name,
                                    fallback_err,
                                )
                                _disable_thinking_in_kwargs(api_kwargs, model, agent.temperature)
                                thinking_enabled = False
                                response = await _api_call_with_retry(
                                    client, api_kwargs, agent_name
                                )
                            else:
                                raise
            else:
                try:
                    response = await _api_call_with_retry(client, api_kwargs, agent_name)
                except Exception as think_err:
                    if thinking_enabled and _is_thinking_error(think_err):
                        logger.warning(
                            "Thinking error for %s, disabling and retrying: %s",
                            agent_name,
                            think_err,
                        )
                        _disable_thinking_in_kwargs(api_kwargs, model, agent.temperature)
                        thinking_enabled = False
                        response = await _api_call_with_retry(client, api_kwargs, agent_name)
                    else:
                        raise

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            total_cache_creation += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0

            # Record success for circuit breaker
            if circuit_breaker:
                circuit_breaker.record_success(model)

            # Agent-call observability (never break the loop on metrics error).
            try:
                from src.services.metrics_service import MetricsService

                MetricsService.record_agent_call(
                    agent_name, model, (time.time() - _api_start) * 1000
                )
            except Exception:
                logger.debug("Failed to record agent-call metric", exc_info=True)

            # Capture thinking from final message
            for block in response.content:
                if block.type == "thinking" and hasattr(block, "thinking"):
                    thinking_chunks.append(block.thinking)

            text_blocks = [b for b in response.content if b.type == "text"]
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            # Emit text blocks as reasoning (non-streamed fallback path)
            if not stream:
                for tb in text_blocks:
                    if tb.text.strip():
                        yield LoopThinking(agent=agent_name, text=tb.text, is_thinking=False)
            elif tool_use_blocks:
                # In streaming mode, emit text blocks between tool calls as reasoning
                for tb in text_blocks:
                    if tb.text.strip():
                        yield LoopThinking(agent=agent_name, text=tb.text, is_thinking=False)

            if not tool_use_blocks:
                text = "".join(b.text for b in text_blocks)
                break

            # Process tool calls
            # Divide response tokens equally across all tools in this response
            _n_tools = len(tool_use_blocks)
            _resp_input = response.usage.input_tokens // _n_tools if _n_tools else 0
            _resp_output = response.usage.output_tokens // _n_tools if _n_tools else 0
            _resp_cache_create = (
                (getattr(response.usage, "cache_creation_input_tokens", 0) or 0) // _n_tools
                if _n_tools
                else 0
            )
            _resp_cache_read = (
                (getattr(response.usage, "cache_read_input_tokens", 0) or 0) // _n_tools
                if _n_tools
                else 0
            )

            tool_results = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input
                tools_called.append(tool_name)

                yield LoopToolCall(agent=agent_name, tool_name=tool_name, tool_input=tool_input)

                # Log tool dispatch
                input_summary = str(tool_input)[:200] if tool_input else "{}"
                logger.info(
                    "[tool] %s → %s | input: %s",
                    agent_name,
                    tool_name,
                    input_summary,
                )

                # Governor pre-hook
                pre_result = await governor_pre_tool_hook(
                    tool_name,
                    tool_input,
                    agent_name,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db_factory=db_factory,
                    services=services,
                    run_id=run_id,
                )

                if not pre_result.get("allowed", True):
                    blocked_msg = {
                        "error": pre_result.get("reason", "Blocked by policy"),
                        "approval_required": pre_result.get("approval_required", False),
                        "approval_id": pre_result.get("approval_id"),
                    }
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(blocked_msg),
                        }
                    )
                    tool_call_details.append(
                        SpanToolCall(
                            tool_name=tool_name,
                            input_data=tool_input if isinstance(tool_input, dict) else {},
                            output_data=blocked_msg,
                            status="blocked",
                            error=pre_result.get("reason", "Blocked by policy"),
                        )
                    )
                    yield LoopToolResult(
                        agent=agent_name,
                        tool_name=tool_name,
                        result=blocked_msg,
                        blocked=True,
                    )
                    continue

                # Capability-scope enforcement (fail-closed) + server resolution
                # in ONE registry lookup. An agent that calls a tool outside its
                # capability_scope is rejected before execution (orthogonal to
                # TrustEngine approval). The resolved `tool_server` is reused by
                # the unavailable-server short-circuit below (C5).
                in_scope, tool_server = await _resolve_tool_scope_and_server(
                    tool_name, agent, db_factory, workspace_id
                )
                if not in_scope:
                    scope_msg = {
                        "error": (
                            f"Agent '{agent_name}' is not permitted to call "
                            f"'{tool_name}' — capability is outside its scope."
                        ),
                    }
                    logger.warning(
                        "[tool] %s DENIED %s — out of capability scope",
                        agent_name,
                        tool_name,
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(scope_msg),
                            "is_error": True,
                        }
                    )
                    tool_call_details.append(
                        SpanToolCall(
                            tool_name=tool_name,
                            input_data=_sanitize_for_span(tool_input)
                            if isinstance(tool_input, dict)
                            else {},
                            output_data=_sanitize_for_span(scope_msg),
                            status="blocked",
                            error=scope_msg["error"][:200],
                        )
                    )
                    yield LoopToolResult(
                        agent=agent_name,
                        tool_name=tool_name,
                        result=scope_msg,
                        blocked=True,
                    )
                    continue

                # Short-circuit: an earlier tool this turn proved this tool's MCP
                # server is unavailable (auth_required). Skip execute_tool_fn
                # entirely — return the cached auth error + terminal steer so the
                # model stops retrying instead of burning a round per tool.
                #
                # PRIMARY KEY = the registered SERVER name (C5): resolved from the
                # registry above, so it matches even tool names that embed no
                # provider substring (`search_messages` → google-workspace).
                # FALLBACK = the provider inferred from the tool NAME — best-effort
                # only; it catches the case where the server could not be resolved
                # but the name happens to carry the provider (e.g. `search_gmail*`).
                tool_provider = provider_for_server(tool_name)
                server_down = tool_server is not None and tool_server in unavailable_servers
                provider_down = tool_provider in unavailable_providers
                if server_down or provider_down:
                    _down_label = tool_server if server_down else tool_provider
                    cached_msg = {
                        "status": "error",
                        "error_code": "auth_required",
                        "error": (
                            f"Integration '{_down_label}' is unavailable this "
                            f"session (needs re-authorization)." + _UNAVAILABLE_STEER
                        ),
                    }
                    logger.info(
                        "[tool] %s SHORT-CIRCUIT %s — '%s' marked unavailable this turn",
                        agent_name,
                        tool_name,
                        _down_label,
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(cached_msg),
                            "is_error": True,
                        }
                    )
                    tool_call_details.append(
                        SpanToolCall(
                            tool_name=tool_name,
                            input_data=_sanitize_for_span(tool_input)
                            if isinstance(tool_input, dict)
                            else {},
                            output_data=_sanitize_for_span(cached_msg),
                            status="error",
                            error=cached_msg["error"][:200],
                        )
                    )
                    yield LoopToolResult(
                        agent=agent_name,
                        tool_name=tool_name,
                        result=cached_msg,
                    )
                    continue

                tool_start = time.time()
                try:
                    result = await asyncio.wait_for(
                        execute_tool_fn(
                            tool_name,
                            tool_input,
                            user_id=user_id,
                            workspace_id=workspace_id,
                        ),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    result = {"error": f"Tool '{tool_name}' timed out after 60s", "timed_out": True}
                    logger.warning("[tool] %s TIMEOUT after 60s", tool_name)
                tool_latency = int((time.time() - tool_start) * 1000)

                # Log tool result
                result_summary = str(result)[:200] if result else "null"
                logger.info(
                    "[tool] %s ← %s | %dms | result: %s",
                    agent_name,
                    tool_name,
                    tool_latency,
                    result_summary,
                )

                # Detect tool errors and signal them to Claude via is_error
                is_error = (
                    isinstance(result, dict)
                    and "error" in result
                    and result.get("status") not in ("ok", "success", "updated", "ingested")
                )

                # Server-unavailable detection: if this tool's MCP server is down
                # for auth reasons, mark its provider so subsequent tools this
                # turn short-circuit, and append a terminal steer telling the
                # model to stop retrying. Handles both the structured
                # auth_required envelope and the legacy bridge-not-init shape.
                steer_suffix = ""
                unavail_provider = _unavailable_provider(result)
                unavail_server = _unavailable_server(result)
                if unavail_provider is not None or unavail_server is not None:
                    # Track BOTH keys: server (primary, C5) and provider
                    # (best-effort fallback for name-based resolution).
                    if unavail_server is not None:
                        unavailable_servers.add(unavail_server)
                    if unavail_provider is not None:
                        unavailable_providers.add(unavail_provider)
                    steer_suffix = _UNAVAILABLE_STEER
                elif (
                    isinstance(result, dict) and result.get("error") == "MCP bridge not initialized"
                ):
                    # Legacy shape carries no provider/server — cannot key the
                    # short-circuit set, but the terminal steer still stops the
                    # retry loop (the primary stop mechanism).
                    steer_suffix = _BRIDGE_NOT_INIT_STEER

                # Tool-call observability (never break the loop on metrics error).
                try:
                    from src.services.metrics_service import MetricsService

                    MetricsService.record_tool_call(
                        tool_name, status="error" if is_error else "success"
                    )
                except Exception:
                    logger.debug("Failed to record tool-call metric", exc_info=True)

                result_content = (
                    json.dumps(result) if isinstance(result, dict) else str(result)
                ) + steer_suffix
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result_content,
                        **({"is_error": True} if (is_error or steer_suffix) else {}),
                    }
                )

                # Redact secrets + truncate before persisting to the trace span.
                # Does NOT affect the live result returned to the loop (above).
                persisted_output: Any = _sanitize_for_span(result)
                persisted_input: Any = (
                    _sanitize_for_span(tool_input) if isinstance(tool_input, dict) else {}
                )

                tool_call_details.append(
                    SpanToolCall(
                        tool_name=tool_name,
                        input_data=persisted_input,
                        output_data=persisted_output,
                        status="error" if is_error else "success",
                        error=result.get("error", "")[:200] if is_error else None,
                        duration_ms=tool_latency,
                    )
                )

                yield LoopToolResult(
                    agent=agent_name,
                    tool_name=tool_name,
                    result=result,
                    latency_ms=tool_latency,
                )

                await audit_post_tool_hook(
                    tool_name,
                    tool_input,
                    result,
                    agent_name,
                    trace_id=trace.trace_id if trace else None,
                    span_id=span.span_id if span else None,
                    tokens_used=_resp_input + _resp_output,
                    latency_ms=tool_latency,
                    db_factory=db_factory,
                    workspace_id=workspace_id,
                )

                # Per-tool cost attribution (Issue #13)
                try:
                    from ulid import ULID

                    from src.models.token_usage import TokenUsage

                    async with db_factory() as tool_db:
                        tool_db.add(
                            TokenUsage(
                                usage_id=f"usage_{ULID()}",
                                workspace_id=workspace_id,
                                agent_name=agent_name,
                                model=model,
                                input_tokens=_resp_input,
                                output_tokens=_resp_output,
                                cache_creation_input_tokens=_resp_cache_create,
                                cache_read_input_tokens=_resp_cache_read,
                                thinking_tokens=0,
                                cost_usd=0.0,
                                trigger=f"tool:{tool_name}",
                                trace_id=trace.trace_id if trace else None,
                            )
                        )
                        await tool_db.commit()
                except Exception:
                    pass  # Non-critical — don't break the agent loop

            # Preserve content blocks for multi-turn continuity
            messages.append(
                {"role": "assistant", "content": _sanitize_content_blocks(response.content)}
            )
            messages.append({"role": "user", "content": tool_results})
        else:
            text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

    except CancellationRequested:
        logger.info("Agent %s cancelled via cancellation token", agent_name)
        raise
    except anthropic.APIError as e:
        logger.error("Claude API error in %s: %s", agent_name, e)
        if circuit_breaker:
            circuit_breaker.record_failure(model)
        text = f"[Agent {agent_name} API error: {e}]"
        yield LoopError(agent=agent_name, message=str(e))
    except Exception as e:
        logger.error("Agent %s failed: %s", agent_name, e, exc_info=True)
        if circuit_breaker:
            circuit_breaker.record_failure(model)
        text = f"[Agent {agent_name} error: {e}]"
        yield LoopError(agent=agent_name, message=str(e))
    finally:
        # Guarantee half-open probe lock is released even if the generator
        # is abandoned (caller break/cancel) without hitting success/failure.
        if circuit_breaker:
            circuit_breaker.reset_half_open_probe(model)

    latency_ms = int((time.time() - start_time) * 1000)

    # Assemble thinking summary for persistence
    thinking_summary = "".join(thinking_chunks) or None

    # Record token usage
    # NOTE: record_from_span() exists on BudgetTracker but cannot be used here
    # because the span hasn't been populated with token counts yet — those are
    # set by trace.end_span() below. The local variables are the source of truth
    # at this point. When span lifecycle is refactored to populate tokens before
    # budget recording, switch to budget.record_from_span(db, span=span, ...).
    cost_usd = 0.0
    try:
        async with db_factory() as db:
            usage = await budget.record_usage(
                db,
                agent_name=agent_name,
                model=model,
                input_tokens=total_input,
                output_tokens=total_output,
                cache_creation_input_tokens=total_cache_creation,
                cache_read_input_tokens=total_cache_read,
                trigger=trace.trigger if trace else "unknown",
                trace_id=trace.trace_id if trace else None,
                workspace_id=workspace_id,
            )
            cost_usd = usage.cost_usd
            await db.commit()
    except Exception as e:
        logger.error("Failed to record token usage: %s", e)

    if span and trace:
        trace.end_span(
            span.span_id,
            input_tokens=total_input,
            output_tokens=total_output,
            cache_creation_input_tokens=total_cache_creation,
            cache_read_input_tokens=total_cache_read,
            tools_called=tools_called,
            tool_call_details=tool_call_details,
            thinking_summary=thinking_summary,
            response_text=text,
            model=model,
            cost_usd=cost_usd,
        )

    yield LoopDone(
        agent=agent_name,
        text=text,
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_tokens=total_cache_creation,
        cache_read_tokens=total_cache_read,
        tools_called=tools_called,
        tool_call_details=tool_call_details,
        thinking_summary=thinking_summary,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
