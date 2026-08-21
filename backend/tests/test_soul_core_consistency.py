"""The shared soul core must be true for EVERY prompt it is prepended to.

`MULDRO_SOUL_CORE` is composed by `build_system_prompt` as
``{soul}\n\n--- YOUR ROLE ---\n{role_prompt}`` for every agent AND for the synthetic chat
`lead`. It used to carry a six-agent org chart — an `<agents>` table plus rules assigning
write tools to the Executor and the user-facing voice to the Presenter.

The lead is neither, and appears in no table. `LEAD_PROMPT`, appended immediately after,
says it owns the whole turn and is the only voice the user hears. So the composed prompt
told the model both that it may not use write tools and may not speak, and that it must do
both — on the path that handles every chat turn.

Those statements were also pure duplication: every role prompt already states its own
boundary, in the second person, next to the reader it is true for. So they belong there and
not in a string shared with a reader they are false for.
"""

from __future__ import annotations

import pytest

from src.orchestrator.prompts import (
    AGENT_PROMPTS,
    EXECUTOR_PROMPT,
    LEAD_PROMPT,
    MULDRO_SOUL_CORE,
    PRESENTER_PROMPT,
)

# Phrases that assign a job to a NAMED OTHER agent. Any of these in the shared core is a
# statement some reader of that core is contradicted by.
_ROLE_MONOPOLY_PHRASES = (
    "Only the Executor",
    "Only Presenter",
    "Only the Presenter",
    "Only Planner",
    "Only the Planner",
)


def _composed(role_prompt: str) -> str:
    """Mirror `AgentInvoker.build_system_prompt`'s composition."""
    return f"{MULDRO_SOUL_CORE}\n\n--- YOUR ROLE ---\n{role_prompt}"


class TestSharedCoreIsTrueForEveryReader:
    def test_core_assigns_no_job_to_a_named_other_agent(self):
        for phrase in _ROLE_MONOPOLY_PHRASES:
            assert phrase not in MULDRO_SOUL_CORE, (
                f"{phrase!r} is in the SHARED core. It is false for at least the chat lead, "
                "which is not in the agent roster and owns its whole turn."
            )

    def test_core_carries_no_agent_roster(self):
        """A roster in the shared core is a promise that every reader is in it."""
        assert "<agents>" not in MULDRO_SOUL_CORE

    def test_the_lead_prompt_is_not_contradicted_by_its_own_core(self):
        composed = _composed(LEAD_PROMPT)
        assert "you own the WHOLE turn" in composed
        for phrase in _ROLE_MONOPOLY_PHRASES:
            assert phrase not in composed

    @pytest.mark.parametrize("name", sorted(AGENT_PROMPTS))
    def test_no_agents_composed_prompt_contradicts_itself(self, name):
        for phrase in _ROLE_MONOPOLY_PHRASES:
            assert phrase not in _composed(AGENT_PROMPTS[name])


class TestNothingWasLostInTheMove:
    """Each statement that left the shared core must survive where it is true."""

    def test_executor_still_owns_the_external_write(self):
        assert "Executor" in EXECUTOR_PROMPT
        assert "only agent" in EXECUTOR_PROMPT.lower()

    def test_presenter_still_owns_the_voice(self):
        assert "ONLY voice" in PRESENTER_PROMPT


class TestTheCoreKeepsWhatIsTrueForEveryone:
    """Regression fence: the behavioural laws are the point of a SHARED core."""

    def test_core_keeps_the_behavioural_laws(self):
        lower = MULDRO_SOUL_CORE.lower()
        assert "operating system" in lower
        assert "never fake certainty" in lower
        assert "fail legibly" in lower
        assert "ask the user" in lower

    def test_core_tells_the_model_what_a_gate_can_do(self):
        """A write gate has three outcomes, and a STAGED action has not happened yet.

        The staged (PREPARE) verdict returns a `status="success"` ToolMessage by design, so
        without this a model can honestly read it as done and report it as done — soul law 1
        broken by the runtime's own success signal.
        """
        lower = MULDRO_SOUL_CORE.lower()
        assert "gated" in lower
        assert "stage" in lower
