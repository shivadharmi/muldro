"""Per-turn cap on the tool-argument repair loop.

``ToolExecutor.execute_tool`` rejects an internal tool call whose arguments fail their
Pydantic model, returning ``{"error": ..., "error_code": "invalid_tool_args"}``.
``muldro_tool_dispatcher`` turns that into ``ToolMessage(status="error")``, and LangGraph
then hands the model another turn — so the model can shorten the offending field and call
again. That repair loop is real (observed live) and, without this middleware, **unbounded**:
no ``recursion_limit`` is configured anywhere, and LangGraph 1.x's default of 10007
super-steps is a cost ceiling, not a design.

This middleware bounds it per tool, per turn. Per-turn isolation is structural, exactly as
in ``unavailable_server``: the factory builds a **fresh** counter dict per call, closed over
by the hook, and ``AgentInvoker._build_deep_agent_for`` builds one middleware instance per
turn — so there is no turn id to track and no state to reset between turns.

Installed immediately outer of the dispatcher (see ``agent_invoker``'s order comment), so it
sees the dispatcher's normalized ``ToolMessage`` and nothing else can have rewritten it.

WHAT THE COUNTER MEASURES, AND WHAT IT DOES NOT
``muldro_tool_arg_repair_total`` is emitted from here because this is the only place that
holds the per-turn context needed to tell a *repair* from a plain first-try success. That
placement is also its limitation: this middleware only sees calls that go through the
deep-runtime tool chain. Other callers reach ``ToolExecutor.execute_tool`` directly and are
invisible to it — ``services/prepared_actions.py`` (a confirmed prepared action replays its
recorded payload through a dispatcher built in ``api/routes_approvals_prepared.py``, with no
middleware chain at all), ``deep_runtime/readback_readfn.py`` and ``services/step_runner.py``
(read-back post-conditions, deliberately bypassing the chain), and
``orchestrator/muldro.py``'s ``_execute_tool`` facade. An ``invalid_tool_args`` rejection on
any of those is NOT counted.

That is stated plainly rather than papered over, because it does not weaken the measurement:
the question being asked is whether the REPAIR LOOP repairs, and the repair loop only exists
where a model gets another turn to try again — which is here. This is not a count of all
argument-validation rejections, and must not be read as one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)

# The error_code ToolExecutor stamps on a failed typed-argument parse.
_INVALID_ARGS_CODE = "invalid_tool_args"

# The error_code this middleware stamps when it refuses to dispatch.
REPAIR_CAP_CODE = "repair_cap_exceeded"

# How many argument-validation failures one tool may accumulate in one turn before its
# next call is refused without dispatch. MAX_ATTEMPTS = 3 means the model gets the
# original call plus TWO repair attempts.
#
# Measured, not guessed — and the measurement is thin. Live ``gpt-5-mini``, two observed
# rejections of ``render_surface``; both were followed by a retry that shortened the
# offending field, and only one of the two retries produced a valid call: ``subtitle`` went
# 145 → 127 (still over the 120 limit) in the first, and 121 → 117 (valid) in the second.
# So a single retry was INSUFFICIENT in 1 of 2 observed cases. n=2 is far too thin to call
# this a distribution; R3b adds the counter that will let us revisit it with real data.
#
# The design spec (``docs/superpowers/specs/2026-08-20-typed-generation-design.md`` §3.4)
# says the repair loop is "capped at one retry". This deliberately deviates by one round,
# because the two errors are not symmetric: being one round too generous costs a model
# round, while being one round too tight costs the rendered surface entirely — the lead
# falls back to chat text and the user never sees the surface it was building.
MAX_ATTEMPTS = 3

# Terminal steer, in the house style of ``unavailable_server._UNAVAILABLE_STEER`` and
# ``tool_executor``'s "…and call the tool again.": say what is wrong AND what to do.
_CAP_STEER = "Stop calling this tool and answer the user in chat text instead."


def _error_code(result: Any) -> str | None:
    """Best-effort read of a ToolMessage payload's ``error_code``.

    ``muldro_tool_dispatcher`` serializes the executor's dict with ``json.dumps``, so the
    content is normally a JSON string; a dict is accepted defensively. Anything else
    (list content blocks, plain prose) yields ``None`` — no cap decision possible.
    """
    if not isinstance(result, ToolMessage):
        return None
    content = result.content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
    elif isinstance(content, dict):
        payload = content
    else:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("error_code")
    return str(code) if code is not None else None


def _record(tool_name: str, outcome: str, attempts: int) -> None:
    """Count one repair-loop outcome, and say the same thing in the log.

    Printf/bracket style, matching ``tool_executor``'s ``[toolargs] %s rejected: %s`` and the
    cap's own ``[repair_cap] refusing %s`` — deliberately NOT ``extra={...}``, because
    ``JSONFormatter`` serializes a 13-key allowlist that contains neither ``tool`` nor
    ``outcome`` (see the counter's comment in ``services/metrics_service``).
    """
    MetricsService.record_tool_arg_repair(tool=tool_name, outcome=outcome)
    logger.info("[repair_cap] %s %s (failures this turn: %d)", tool_name, outcome, attempts)


def make_repair_cap_middleware() -> AgentMiddleware:
    """Build the per-turn tool-argument repair-loop cap.

    Returns:
        An ``AgentMiddleware`` whose ``awrap_tool_call`` hook counts per-tool
        ``invalid_tool_args`` failures for this turn and, at ``MAX_ATTEMPTS``, refuses
        further calls to that tool **without dispatching** them.

    Per-turn state is this closure-local dict — a fresh middleware instance always starts
    empty, which is the whole per-turn story (see module docstring).
    """
    failures: dict[str, int] = {}

    @wrap_tool_call
    async def repair_cap(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_call = request.tool_call
        tool_name = tool_call["name"]

        # deepagents built-ins (write_todos, ls, task, …) are framework scaffolding whose
        # result may be a ``Command``, not a ToolMessage, and which never route through
        # ToolExecutor's typed-argument parse. Skip exactly like every sibling
        # wrap_tool_call middleware, per src/deep_runtime/builtins.py.
        if tool_name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        if failures.get(tool_name, 0) >= MAX_ATTEMPTS:
            logger.warning(
                "[repair_cap] refusing %s after %d argument-validation failures this turn",
                tool_name,
                MAX_ATTEMPTS,
            )
            # Counted here rather than through ``_record`` because the warning above already
            # IS this outcome's log line; a second one would only duplicate it. Every capped
            # call counts, not just the first: a model that keeps reaching for a dead tool is
            # exactly the wasted-round cost this counter exists to expose. It is deliberately
            # NOT also a ``rejected`` — nothing was dispatched, so nothing was rejected, and
            # double-counting would corrupt the denominator of ``repaired / rejected``.
            MetricsService.record_tool_arg_repair(tool=tool_name, outcome="exhausted")
            capped = {
                "error": (
                    f"Tool '{tool_name}' has failed argument validation "
                    f"{MAX_ATTEMPTS} times this turn. " + _CAP_STEER
                ),
                "error_code": REPAIR_CAP_CODE,
            }
            return ToolMessage(
                content=json.dumps(capped),
                tool_call_id=tool_call["id"],
                name=tool_name,
                status="error",
            )

        # No try/except: the dispatcher deliberately lets GraphInterrupt propagate, and
        # this layer must not become the thing that swallows it.
        result = await handler(request)

        if _error_code(result) == _INVALID_ARGS_CODE:
            failures[tool_name] = failures.get(tool_name, 0) + 1
            _record(tool_name, "rejected", failures[tool_name])
        elif isinstance(result, ToolMessage) and result.status != "error":
            # The model repaired the call — forgive the earlier failures rather than
            # letting a turn-long tally cap a tool that is now working.
            prior = failures.pop(tool_name, 0)
            # Only a success that FOLLOWED a failure is a repair. A tool that worked first
            # time never entered the loop, and counting it would inflate the numerator of
            # the very ratio this counter exists to measure.
            if prior:
                _record(tool_name, "repaired", prior)

        return result

    return repair_cap
