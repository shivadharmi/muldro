"""A prompt's brace escaping must match whether it is actually `.format()`ed.

`AgentInvoker.build_system_prompt` formats ONLY the planner prompt (to inject
`{capability_summary}`). Every other prompt reaches the model verbatim, so a `{{` in one of
them is not an escape — it is two literal brace characters in a JSON template the model is
being asked to copy.

Only `{{` is asserted, deliberately. `.format()` escaping is symmetric — you never escape a
closing brace without escaping its opening one — so `{{`-absence is a COMPLETE detector for
an unformatted prompt authored with escapes. `}}` is not: it occurs naturally at the tail of
nested JSON (`{"properties": {"variant": "heading"}}`), and PRESENTER_VOICE has five such
occurrences and zero `{{`. Asserting on `}}` would fail on correct prose.
"""

from __future__ import annotations

import pytest

from src.orchestrator import prompts

# The ONLY prompt that build_system_prompt calls .format() on.
UNFORMATTED = [
    "MULDRO_SOUL_CORE",
    "LIBRARIAN_PROMPT",
    "PERCEIVER_PROMPT",
    "EXECUTOR_PROMPT",
    "PRESENTER_VOICE",
    "PRESENTER_PROMPT",
    "PERSONA_PROMPT",
    "LEAD_PROMPT",
    "LEAD_PROMPT_PLANLESS",
]


@pytest.mark.parametrize("name", UNFORMATTED)
def test_unformatted_prompts_have_no_escaped_braces(name):
    text = getattr(prompts, name)
    assert "{{" not in text, (
        f"{name} contains '{{{{' but is never .format()ed, so the model sees two literal "
        "brace characters in what it is told is a JSON template"
    )


def test_the_formatted_prompt_still_escapes_its_braces():
    """Teeth: the planner prompt IS formatted, so its JSON braces must stay escaped or
    `.format()` raises KeyError on the JSON keys."""
    assert "{{" in prompts.PLANNER_PROMPT_V2
    assert "{capability_summary}" in prompts.PLANNER_PROMPT_V2


def test_the_planner_prompt_still_formats():
    """The real call `build_system_prompt` makes must not raise."""
    prompts.PLANNER_PROMPT_V2.format(capability_summary="probe")


def test_no_prompt_endorses_doubled_braces_in_prose():
    """The Perceiver said 'use literal braces' and then showed `{{`."""
    for name in UNFORMATTED:
        assert "literal braces" not in getattr(prompts, name).lower(), (
            f"{name} instructs the model about brace literals; after unescaping, that "
            "sentence describes a problem that no longer exists"
        )
