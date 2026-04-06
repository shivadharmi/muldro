"""Unified agent loop — single implementation for streaming and non-streaming.

Extracts the duplicated logic from JarvisOrchestrator._call_agent() and
_call_agent_stream() into a single async generator that yields typed events.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.orchestrator.contracts import SpanToolCall
from src.orchestrator.hooks import audit_post_tool_hook, governor_pre_tool_hook

logger = logging.getLogger(__name__)


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
) -> AsyncGenerator[LoopEvent, None]:
    """Core agent loop — yields LoopEvent instances.

    When stream=True, uses messages.stream() for token-by-token output.
    When stream=False, uses messages.create() (still yields same events).
    """
    agent_name = agent.name
    span = trace.start_span(agent_name) if trace else None

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
            api_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": agent.max_tokens,
                "system": system_blocks,
                "messages": messages,
            }

            if thinking_enabled:
                api_kwargs["temperature"] = 1  # required when thinking is enabled
                api_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget_tokens,
                }
            else:
                api_kwargs["temperature"] = agent.temperature

            if tools:
                api_kwargs["tools"] = tools

            # Governor structured output: force tool_choice for governor.
            # Forced tool_choice is incompatible with thinking — disable it.
            if agent_name == "governor" and tools:
                governor_tool = next(
                    (t for t in tools if t["name"] == "report_governor_verdict"), None
                )
                if governor_tool:
                    api_kwargs["tool_choice"] = {
                        "type": "tool",
                        "name": "report_governor_verdict",
                    }
                    api_kwargs.pop("thinking", None)
                    if "temperature" not in api_kwargs:
                        api_kwargs["temperature"] = agent.temperature

            response = None

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
                            api_kwargs["temperature"] = agent.temperature
                            api_kwargs.pop("thinking", None)
                            _strip_thinking_from_messages(api_kwargs.get("messages", []))
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
                                api_kwargs["temperature"] = agent.temperature
                                api_kwargs.pop("thinking", None)
                                _strip_thinking_from_messages(api_kwargs.get("messages", []))
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
                        api_kwargs["temperature"] = agent.temperature
                        api_kwargs.pop("thinking", None)
                        _strip_thinking_from_messages(api_kwargs.get("messages", []))
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

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": json.dumps(result) if isinstance(result, dict) else str(result),
                        **({"is_error": True} if is_error else {}),
                    }
                )

                # Truncate large results for persistence
                persisted_output: Any = result
                if isinstance(result, str) and len(result) > 2000:
                    persisted_output = result[:2000] + "...[truncated]"
                elif isinstance(result, dict):
                    result_str = json.dumps(result, default=str)
                    if len(result_str) > 2000:
                        persisted_output = {"_truncated": result_str[:2000]}

                tool_call_details.append(
                    SpanToolCall(
                        tool_name=tool_name,
                        input_data=tool_input if isinstance(tool_input, dict) else {},
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
                    latency_ms=tool_latency,
                    db_factory=db_factory,
                    workspace_id=workspace_id,
                )

            # Preserve content blocks for multi-turn continuity
            messages.append(
                {"role": "assistant", "content": _sanitize_content_blocks(response.content)}
            )
            messages.append({"role": "user", "content": tool_results})
        else:
            text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

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
    thinking_summary = "".join(thinking_chunks)
    if len(thinking_summary) > 5000:
        thinking_summary = thinking_summary[:5000] + "...[truncated]"
    thinking_summary = thinking_summary or None

    # Record token usage
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
