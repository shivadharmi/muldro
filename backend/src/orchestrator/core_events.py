"""Typed event vocabulary for the unified chat-orchestration core (ORCH-P1-1).

`JarvisOrchestrator._process_core` drives the single intent → plan → route →
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


class AgentStreamEvent(_CoreEventBase):
    """Pass-through wrapper for a single ``_call_agent_stream`` event (agent_start,
    thinking, text_delta, tool_call, tool_result, agent_done, error). The payload
    is already a client-safe, well-formed SSE dict — the stream adapter yields it
    verbatim; the batch adapter inspects ``agent_done`` for the final text."""

    type: Literal["agent_stream"] = "agent_stream"
    payload: dict[str, Any]


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
        AgentStreamEvent,
        StepResult,
        SystemStepResult,
        StepError,
        PlanModeStepSkipped,
        UserActionsReady,
        Presentation,
        RunCompleted,
        RunFailed,
    ],
    Field(discriminator="type"),
]


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
        case AgentStreamEvent(payload=payload):
            return payload
        case StepError(step_id=step_id, error=error):
            return {"event": "step_error", "step_id": step_id, "error": error}
        case PlanModeStepSkipped(plan_id=plan_id, message=message):
            return {"event": "plan_ready", "plan_id": plan_id, "message": message}
        case UserActionsReady(steps=steps):
            return {"event": "user_actions", "steps": steps}
        case Presentation(text=text):
            return {"event": "response", "text": text}
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
