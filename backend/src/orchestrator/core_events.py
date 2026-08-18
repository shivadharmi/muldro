"""Typed event vocabulary for the unified chat-orchestration core (ORCH-P1-1).

`MuldroOrchestrator._process_core` drives the single intent → plan → route →
execute → present → surface → learn pipeline and yields these ``CoreEvent``s.
Two thin adapters consume the same stream:

* ``process_message_stream`` translates each event to its SSE dict (token-level
  ``AgentStreamEvent``s pass through; lifecycle events map to the existing SSE
  names) — see :func:`core_event_to_sse`.
* ``process_message`` folds the events into the batch ``result`` dict.

The vocabulary is a **superset** of both shells' needs: some events are
stream-only (their SSE mapping is the point), some are batch-only (``to_sse``
returns ``None`` so the stream adapter drops them), and ``AgentStreamEvent``
serves both. Modelled as a Pydantic discriminated union (engineering-standards
§1: contracts at every boundary; discriminated unions over ``event["type"]``
string-matching) so the SSE/batch mappings dispatch on type, never on a bare
``dict.get("event")``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _CoreEventBase(BaseModel):
    """Frozen base for every core event (immutability by default)."""

    model_config = ConfigDict(frozen=True)


# ── Lifecycle ────────────────────────────────────────────────────────────────


class TraceStarted(_CoreEventBase):
    type: Literal["trace_started"] = "trace_started"
    trace_id: str


class IntentClassified(_CoreEventBase):
    type: Literal["intent_classified"] = "intent_classified"
    intent: str
    confidence: float


class InteractionLogged(_CoreEventBase):
    """Audit-log id for this turn. Batch folds it into ``interaction_id``; the
    stream path logs but never surfaces the id, so this is stream-dropped."""

    type: Literal["interaction_logged"] = "interaction_logged"
    interaction_id: str


class PlanReady(_CoreEventBase):
    """The plan is finalized. ``summary`` is batch-only (the batch ``result``
    carries it; the SSE ``plan`` event does not)."""

    type: Literal["plan_ready"] = "plan_ready"
    plan: dict[str, Any]
    run_id: str | None = None
    summary: str = ""


# ── Execution ────────────────────────────────────────────────────────────────


class AgentStarted(_CoreEventBase):
    type: Literal["agent_started"] = "agent_started"
    agent: str
    model: str | None = None


class AgentThinking(_CoreEventBase):
    type: Literal["agent_thinking"] = "agent_thinking"
    agent: str
    text: str = ""
    is_thinking: bool = False


class AgentTextDelta(_CoreEventBase):
    type: Literal["agent_text_delta"] = "agent_text_delta"
    agent: str
    text: str = ""


class AgentToolCall(_CoreEventBase):
    type: Literal["agent_tool_call"] = "agent_tool_call"
    agent: str
    tool: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class AgentToolResult(_CoreEventBase):
    type: Literal["agent_tool_result"] = "agent_tool_result"
    agent: str
    tool: str = ""
    result: Any = None
    blocked: bool = False
    latency_ms: int = 0


class AgentDone(_CoreEventBase):
    """Final agent-loop event carrying the response text plus token/cost
    telemetry. ``routes_chat`` folds these fields into the persisted message;
    the batch adapter reads ``text`` for the step result."""

    type: Literal["agent_done"] = "agent_done"
    agent: str
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    tools_called: list[str] | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None


class AgentStreamEvent(_CoreEventBase):
    """Fallback pass-through for a ``_call_agent_stream`` dict that isn't one of
    the typed token events above — i.e. the rare error frames (``Unknown agent``
    and the sanitized ``LoopError``). The payload is already a client-safe SSE
    dict; the stream adapter yields it verbatim."""

    type: Literal["agent_stream"] = "agent_stream"
    payload: dict[str, Any]


class ValidationFailed(_CoreEventBase):
    """Input validation rejected the request before the pipeline started. Maps to
    the bare ``{"event": "error", "message": ...}`` SSE frame (no code / cid —
    distinct from a mid-pipeline :class:`RunFailed`)."""

    type: Literal["validation_failed"] = "validation_failed"
    message: str


class StepResult(_CoreEventBase):
    """Final text of one agent step, keyed as the batch ``result`` expects
    (``step_{idx}_{capability}``). Batch-only — on the stream path the
    ``agent_done`` pass-through already carried this text."""

    type: Literal["step_result"] = "step_result"
    key: str
    output: str


class SystemStepResult(_CoreEventBase):
    """Result of a ``system.*`` step, keyed ``system_{capability}``. Batch folds
    it into ``result``; the stream path discards system results, so this is
    stream-dropped."""

    type: Literal["system_step_result"] = "system_step_result"
    key: str
    output: Any


class StepError(_CoreEventBase):
    """A step could not be routed (no agent/tools for its capability). Stream
    emits ``step_error``; batch folds ``error_{step_id}``."""

    type: Literal["step_error"] = "step_error"
    step_id: str
    error: str


class PlanModeStepSkipped(_CoreEventBase):
    """Plan-mode skipped a risky step instead of executing it. Stream emits
    ``plan_ready``; batch-relevant only once batch adopts ``mode="plan"``."""

    type: Literal["plan_mode_step_skipped"] = "plan_mode_step_skipped"
    plan_id: str | None
    message: str


# ── Terminal ─────────────────────────────────────────────────────────────────


class UserActionsReady(_CoreEventBase):
    type: Literal["user_actions_ready"] = "user_actions_ready"
    steps: list[dict[str, Any]]


class Presentation(_CoreEventBase):
    """The user-facing reply text, already scrubbed of fenced surface blocks
    (surface extraction happens inside the core before this is emitted)."""

    type: Literal["presentation"] = "presentation"
    text: str


class ApprovalRequired(_CoreEventBase):
    """The chat single-lead turn SUSPENDED for the user's confirmation (P2.3).

    Emitted when the action-time ``permission_gate`` pauses a write (``ask``/``auto``
    mode). It ENDS the turn without running the completion tail — the paused deep
    checkpoint stays live and the resume path (a later task) re-enters the thread and
    runs the tail on the terminal reply. The fields are exactly those the gate's
    ``approval_needed`` interrupt frame carries; ``reversible``/``blast_radius`` live on
    the persisted ``Approval`` row (a later frontend concern), not here.

    Its SSE mapping (``core_event_to_sse``) is EXPLICIT (never batch-only): a dropped
    frame would strand the paused checkpoint forever, so the streaming mapping must
    always emit it."""

    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    capability: str
    risk_level: str
    thread_id: str


class RunCompleted(_CoreEventBase):
    type: Literal["run_completed"] = "run_completed"
    trace_id: str
    run_id: str | None = None
    surface_id: str | None = None


class RunFailed(_CoreEventBase):
    """Pipeline raised. Carries the already-classified, client-safe pieces so each
    adapter builds its own shape (stream: ``{"event":"error",...}``; batch:
    ``{"decision":"error","summary":...}``)."""

    type: Literal["run_failed"] = "run_failed"
    trace_id: str
    code: str
    message: str
    correlation_id: str


CoreEvent = Annotated[
    Union[
        TraceStarted,
        IntentClassified,
        InteractionLogged,
        PlanReady,
        AgentStarted,
        AgentThinking,
        AgentTextDelta,
        AgentToolCall,
        AgentToolResult,
        AgentDone,
        AgentStreamEvent,
        StepResult,
        SystemStepResult,
        StepError,
        PlanModeStepSkipped,
        UserActionsReady,
        Presentation,
        ApprovalRequired,
        RunCompleted,
        RunFailed,
        ValidationFailed,
    ],
    Field(discriminator="type"),
]


def agent_event_from_sse(evt: dict[str, Any]) -> CoreEvent:
    """Map a single ``_call_agent_stream`` SSE dict to its typed ``CoreEvent``.

    The six metadata-bearing token events become typed; anything else (the rare
    ``Unknown agent`` and sanitized ``LoopError`` error frames) is wrapped as a
    pass-through :class:`AgentStreamEvent` so its exact shape survives. This keeps
    ``_call_agent_stream`` itself untouched (dicts in, sanitization preserved)
    while giving ``routes_chat`` typed events to fold.
    """
    kind = evt.get("event")
    agent = evt.get("agent", "")
    if kind == "agent_start":
        return AgentStarted(agent=agent, model=evt.get("model"))
    if kind == "thinking":
        return AgentThinking(
            agent=agent, text=evt.get("text", ""), is_thinking=evt.get("is_thinking", False)
        )
    if kind == "text_delta":
        return AgentTextDelta(agent=agent, text=evt.get("text", ""))
    if kind == "tool_call":
        return AgentToolCall(agent=agent, tool=evt.get("tool", ""), input=evt.get("input", {}))
    if kind == "tool_result":
        return AgentToolResult(
            agent=agent,
            tool=evt.get("tool", ""),
            result=evt.get("result"),
            blocked=evt.get("blocked", False),
            latency_ms=evt.get("latency_ms", 0),
        )
    if kind == "agent_done":
        return AgentDone(
            agent=agent,
            text=evt.get("text", ""),
            input_tokens=evt.get("input_tokens"),
            output_tokens=evt.get("output_tokens"),
            cache_creation_tokens=evt.get("cache_creation_tokens"),
            cache_read_tokens=evt.get("cache_read_tokens"),
            tools_called=evt.get("tools_called"),
            latency_ms=evt.get("latency_ms"),
            cost_usd=evt.get("cost_usd"),
        )
    return AgentStreamEvent(payload=evt)


def core_event_to_sse(event: CoreEvent) -> dict[str, Any] | None:
    """Translate a ``CoreEvent`` to its SSE dict, or ``None`` if the event is
    batch-only (the stream adapter drops ``None``).

    The exact dict shapes here are a frozen contract: ``routes_chat.py`` and the
    web client consume them by name. Changing a key breaks the streaming chat UI.
    """
    match event:
        case TraceStarted(trace_id=trace_id):
            return {"event": "trace", "trace_id": trace_id}
        case IntentClassified(intent=intent, confidence=confidence):
            return {"event": "intent", "intent": intent, "confidence": confidence}
        case PlanReady(plan=plan, run_id=run_id):
            return {"event": "plan", "plan": plan, "run_id": run_id}
        case AgentStarted(agent=agent, model=model):
            return {"event": "agent_start", "agent": agent, "model": model}
        case AgentThinking(agent=agent, text=text, is_thinking=is_thinking):
            return {"event": "thinking", "agent": agent, "text": text, "is_thinking": is_thinking}
        case AgentTextDelta(agent=agent, text=text):
            return {"event": "text_delta", "agent": agent, "text": text}
        case AgentToolCall(agent=agent, tool=tool, input=tool_input):
            return {"event": "tool_call", "agent": agent, "tool": tool, "input": tool_input}
        case AgentToolResult(
            agent=agent, tool=tool, result=tool_result, blocked=blocked, latency_ms=latency_ms
        ):
            return {
                "event": "tool_result",
                "agent": agent,
                "tool": tool,
                "result": tool_result,
                "blocked": blocked,
                "latency_ms": latency_ms,
            }
        case AgentDone():
            return {
                "event": "agent_done",
                "agent": event.agent,
                "text": event.text,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cache_creation_tokens": event.cache_creation_tokens,
                "cache_read_tokens": event.cache_read_tokens,
                "tools_called": event.tools_called,
                "latency_ms": event.latency_ms,
                "cost_usd": event.cost_usd,
            }
        case AgentStreamEvent(payload=payload):
            return payload
        case ValidationFailed(message=message):
            return {"event": "error", "message": message}
        case StepError(step_id=step_id, error=error):
            return {"event": "step_error", "step_id": step_id, "error": error}
        case PlanModeStepSkipped(plan_id=plan_id, message=message):
            return {"event": "plan_ready", "plan_id": plan_id, "message": message}
        case UserActionsReady(steps=steps):
            return {"event": "user_actions", "steps": steps}
        case Presentation(text=text):
            return {"event": "response", "text": text}
        case ApprovalRequired(
            approval_id=approval_id,
            capability=capability,
            risk_level=risk_level,
            thread_id=thread_id,
        ):
            # The frozen pause frame the frontend consumes to render the confirmation
            # prompt AND to keep the paused checkpoint resumable — key-identical to the
            # deep stream adapter's ``approval_needed`` frame (minus ``agent``).
            return {
                "event": "approval_needed",
                "approval_id": approval_id,
                "capability": capability,
                "risk_level": risk_level,
                "thread_id": thread_id,
            }
        case RunCompleted(trace_id=trace_id, run_id=run_id, surface_id=surface_id):
            done: dict[str, Any] = {"event": "done", "trace_id": trace_id, "run_id": run_id}
            if surface_id:
                done["surface_id"] = surface_id
            return done
        case RunFailed(code=code, message=message, correlation_id=correlation_id):
            return {
                "event": "error",
                "code": code,
                "message": message,
                "correlation_id": correlation_id,
            }
        case InteractionLogged() | StepResult() | SystemStepResult():
            # Batch-only events — the stream path never surfaced these.
            return None
    return None  # pragma: no cover - exhaustive above; satisfies type-checker
