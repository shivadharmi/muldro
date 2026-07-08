"""Deep-runtime inline read-back verifier middleware (Step 7C, spec §4.5).

Placed INNER of write_lock, OUTER of dispatcher — the last policy hop before the tool runs:
  ... → trust_gate → write_lock → readback → dispatcher
So it runs the write via handler(request) (the dispatcher executes it), then — for an
irreversible/external write — reads the effect back and ANNOTATES the verdict onto the
ToolMessage content (a content-JSON key, NEVER `status`, so the SSE frame does not flip to
blocked). CONTRADICTED → an escalate-first divergence payload (the compensator is offered, never
auto-run). CONFIRMED + a gated authorization_source → the deep trust-increment (injected).

Reuses src.services.verification verbatim. `read_fn` defaults to None (the deferred-tick template:
every irreversible write with a post-condition resolves to UNVERIFIED — never CONTRADICTED — until a
live per-connector read seam lands; a real read_fn would false-CONTRADICT the mock-only
calendar.create). Tests inject a mock read_fn + post-condition to exercise CONFIRMED/CONTRADICTED.

DORMANT: added to the chain only when settings.deep_readback_enabled (default False).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.authorization import is_gated_source
from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.capabilities import is_read_only_capability
from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import ReadBackVerifier, ReadFn, VerifyVerdict

logger = logging.getLogger(__name__)

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]
AssessRiskFn = Callable[[str, dict], Awaitable[Any]]
RecordConfirmedFn = Callable[..., Awaitable[None]]


def _annotate(content: Any, verification: dict) -> str:
    """Add a `verification` content-JSON key (never touch status). default=str so a
    non-serializable content never raises (the critique middleware's discipline)."""
    try:
        obj = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, ValueError):
        obj = {"result": content}
    if not isinstance(obj, dict):
        obj = {"result": obj}
    obj["verification"] = verification
    return json.dumps(obj, default=str)


def make_readback_middleware(
    *,
    workspace_id: str,
    authorization_source: str,
    resolve_capability: ResolveCapabilityFn,
    assess_risk: AssessRiskFn,
    read_fn: ReadFn | None = None,
    record_confirmed_outcome: RecordConfirmedFn | None = None,
) -> AgentMiddleware:
    """Build the per-turn read-back middleware. `resolve_capability(name)->capability|None` and
    `assess_risk(capability, args)->risk` reuse the shared per-turn closures. `read_fn` is the
    injected verification seam (None on the dormant path). `record_confirmed_outcome(*, capability,
    risk_level)` fires the trust-increment only for gated writes (None = no-op)."""
    verifier = ReadBackVerifier(read_fn)

    @wrap_tool_call
    async def readback(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        result = await handler(request)
        # A blocked/contended/failed write (trust_gate reject, write_lock contention, dispatcher
        # error) carries status=="error" — nothing to verify, pass through unchanged.
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result

        capability = await resolve_capability(name)
        if not capability or is_read_only_capability(capability):
            return result  # reads are never read-back-verified

        risk = await assess_risk(capability, request.tool_call.get("args") or {})
        write_input = request.tool_call.get("args") or {}
        content = result.content
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        write_output = parsed if isinstance(parsed, dict) else {}

        verdict = await verifier.verify_step(
            capability=capability, write_input=write_input, write_output=write_output, risk=risk
        )
        verification: dict[str, Any] = {"verdict": verdict.value}

        if verdict == VerifyVerdict.CONTRADICTED:
            verification["escalation"] = build_divergence_escalation(
                capability=capability,
                artifact_ref=write_output or {},
                observed="read-back could not confirm the effect",
            )
            logger.warning(
                "[deep_runtime] read-back CONTRADICTED for %s (%s) — escalate-first",
                name,
                capability,
            )
        elif verdict == VerifyVerdict.CONFIRMED and is_gated_source(authorization_source):
            if record_confirmed_outcome is not None:
                await record_confirmed_outcome(
                    capability=capability, risk_level=getattr(risk, "risk_level", "high")
                )

        return result.model_copy(update={"content": _annotate(result.content, verification)})

    return readback
