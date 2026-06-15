"""Single-read Presenter-skip optimisation.

Why this exists: the biggest latency complaint in the OSS-release audit is that
*every* message — even "what's on my calendar?" — pays for a Presenter LLM call
to reformat an answer the read agent already produced. The Perceiver returns a
structured JSON result whose `synthesis` field is already a conversational
narrative, so for a plan that is one read-only Perceiver step we can return that
synthesis directly and skip the Presenter entirely.

These helpers are pure and module-level on purpose: both `process_message` and
`process_message_stream` call the exact same logic, so the two code paths cannot
drift apart in what counts as "a single read" or how the answer is extracted.
"""

import json

from src.contracts import PlanStep

# step_routing entries are (step, agent_name, tools). System steps carry an
# empty agent_name; user steps are collected separately by the caller.
StepRouting = list[tuple[PlanStep, str, list[dict]]]


def single_read_step(step_routing: StepRouting, user_steps: list[PlanStep]) -> PlanStep | None:
    """Return the lone executable step iff the plan is a single read-only
    Perceiver step with no user actions; otherwise None.

    A plan qualifies when, ignoring system steps (empty agent_name), exactly one
    step routes to the Perceiver and there are no user-action steps. User actions
    must still be surfaced by the Presenter, so their presence disqualifies.
    """
    if user_steps:
        return None

    executable = [
        (step, agent)
        for (step, agent, _tools) in step_routing
        if agent and not step.capability.startswith("system.")
    ]
    if len(executable) == 1 and executable[0][1] == "perceiver":
        return executable[0][0]
    return None


def extract_perceiver_synthesis(raw_text: str | None) -> str | None:
    """Return the user-presentable `synthesis` prose from a Perceiver JSON result.

    Tolerates markdown fences and leading/trailing prose around the JSON object.
    Returns None when the text is not parseable Perceiver JSON or has no non-empty
    synthesis — the caller falls back to the Presenter in that case (graceful
    degradation, never a silent empty reply).
    """
    if not raw_text:
        return None

    obj = _parse_json_object(raw_text)
    if obj is None:
        return None

    synthesis = obj.get("synthesis")
    if not isinstance(synthesis, str):
        return None
    synthesis = synthesis.strip()
    return synthesis or None


def _parse_json_object(text: str) -> dict | None:
    """Best-effort parse of a single JSON object embedded in `text`."""
    candidate = text.strip()
    # Strip a leading ```json / ``` fence if present.
    if candidate.startswith("```"):
        candidate = candidate.split("```", 2)[1] if "```" in candidate[3:] else candidate
        candidate = candidate.removeprefix("json").strip().strip("`").strip()

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to the first {...last } span.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
