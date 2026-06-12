"""Tests for the single-read Presenter-skip latency optimisation.

For a plan that is one read-only Perceiver step, the Presenter LLM call is
skipped and the Perceiver's own `synthesis` prose is returned directly.
"""

from src.orchestrator.contracts import PlanStep
from src.orchestrator.presenter_skip import (
    extract_perceiver_synthesis,
    single_read_step,
)


def _routing(*entries):
    """entries: (capability, agent_name) → step_routing tuples (tools empty)."""
    out = []
    for i, (cap, agent) in enumerate(entries):
        step = PlanStep(step_id=f"s{i}", description=cap, capability=cap)
        out.append((step, agent, []))
    return out


# --------------------------------------------------------------------------- #
# single_read_step
# --------------------------------------------------------------------------- #


def test_single_perceive_step_is_detected():
    routing = _routing(("perceive", "perceiver"))
    step = single_read_step(routing, user_steps=[])
    assert step is not None
    assert step.capability == "perceive"


def test_read_capability_routed_to_perceiver_is_detected():
    routing = _routing(("email.search", "perceiver"))
    assert single_read_step(routing, user_steps=[]) is not None


def test_two_executable_steps_is_not_single_read():
    routing = _routing(("perceive", "perceiver"), ("respond", "presenter"))
    assert single_read_step(routing, user_steps=[]) is None


def test_write_step_is_not_single_read():
    routing = _routing(("email.send", "operator"))
    assert single_read_step(routing, user_steps=[]) is None


def test_reason_step_alone_is_not_single_read():
    routing = _routing(("reason", "presenter"))
    assert single_read_step(routing, user_steps=[]) is None


def test_system_step_does_not_count_as_executable():
    # A system step (agent_name="") alongside one read is still a single read.
    routing = _routing(("system.respond", ""), ("perceive", "perceiver"))
    assert single_read_step(routing, user_steps=[]) is not None


def test_user_actions_present_keeps_presenter():
    routing = _routing(("perceive", "perceiver"))
    user_step = PlanStep(step_id="u1", description="sign doc", actor="user", capability="reason")
    assert single_read_step(routing, user_steps=[user_step]) is None


# --------------------------------------------------------------------------- #
# extract_perceiver_synthesis
# --------------------------------------------------------------------------- #


def test_extracts_synthesis_from_valid_json():
    raw = '{"query": "q", "findings": [], "synthesis": "You have 2 meetings today.", "gaps": []}'
    assert extract_perceiver_synthesis(raw) == "You have 2 meetings today."


def test_extracts_synthesis_from_fenced_json():
    raw = '```json\n{"synthesis": "No new emails.", "findings": [], "gaps": []}\n```'
    assert extract_perceiver_synthesis(raw) == "No new emails."


def test_extracts_synthesis_with_leading_prose():
    raw = 'Here are the results:\n{"synthesis": "3 PRs are open.", "findings": []}\nDone.'
    assert extract_perceiver_synthesis(raw) == "3 PRs are open."


def test_empty_synthesis_returns_none():
    raw = '{"synthesis": "   ", "findings": [], "gaps": []}'
    assert extract_perceiver_synthesis(raw) is None


def test_missing_synthesis_returns_none():
    raw = '{"findings": [], "gaps": ["could not reach calendar"]}'
    assert extract_perceiver_synthesis(raw) is None


def test_non_json_returns_none():
    assert extract_perceiver_synthesis("just some plain text answer") is None


def test_empty_input_returns_none():
    assert extract_perceiver_synthesis("") is None
    assert extract_perceiver_synthesis(None) is None
