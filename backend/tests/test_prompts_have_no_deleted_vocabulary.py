"""No prompt may teach a concept the codebase has deleted.

A model reading `PERCEIVER_PROMPT` currently learns it is a merge of "the Observer" and "the
Researcher" — agents that no longer exist. `EXECUTOR_PROMPT` refers to "the Planner's
decision", vocabulary from the removed `PlannerOutput` decision-type era. CLAUDE.md's Common
Mistakes lists both as do-not-reference.

A phrase may carry an allowlist: `PLANNER_PROMPT_V2` names "decision type" twice, both times to
FORBID it ("Your job is NOT to classify a user request into a fixed decision type", "NO
DECISION TYPES: Do NOT output old decision classification strings"). A prohibition is not a
teaching, and deleting it would remove the guardrail rather than the drift.
"""

from __future__ import annotations

import pytest

from src.orchestrator.prompts import AGENT_PROMPTS, LEAD_PROMPT, MULDRO_SOUL_CORE

# (phrase, why it is dead, prompts allowed to name it because they FORBID it)
DELETED_VOCABULARY = [
    ("Observer", "merged into the Perceiver; the agent no longer exists", frozenset()),
    ("Researcher", "merged into the Perceiver; the agent no longer exists", frozenset()),
    ("Planner's decision", "decision types were deleted with PlannerOutput", frozenset()),
    ("PlannerOutput", "replaced by PlanOutput", frozenset()),
    (
        "decision type",
        "replaced by capability-based steps",
        # The planner prompt names it only to prohibit it, twice. Removing those lines would
        # delete the guardrail, not the drift.
        frozenset({"planner"}),
    ),
]

ALL_PROMPTS = {**AGENT_PROMPTS, "lead": LEAD_PROMPT, "soul_core": MULDRO_SOUL_CORE}


@pytest.mark.parametrize("phrase,reason,allowed", DELETED_VOCABULARY)
def test_no_prompt_teaches_deleted_vocabulary(phrase, reason, allowed):
    offenders = [n for n, p in ALL_PROMPTS.items() if phrase in p and n not in allowed]
    assert offenders == [], f"{offenders} still teach {phrase!r} — {reason}"


@pytest.mark.parametrize("phrase,reason,allowed", [e for e in DELETED_VOCABULARY if e[2]])
def test_an_allowlisted_prompt_really_does_still_name_the_phrase(phrase, reason, allowed):
    """Teeth on the allowlist itself: if the prohibition is ever deleted, the allowlist
    becomes a silent hole that would let the teaching back in. Fail so it gets removed."""
    for name in allowed:
        assert phrase in ALL_PROMPTS[name], (
            f"{name} is allowlisted for {phrase!r} but no longer contains it — drop it from "
            "the allowlist so the fence closes again"
        )
