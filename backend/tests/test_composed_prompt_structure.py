"""No structural tag may appear twice in a prompt as the model actually receives it.

The unit that matters is the COMPOSED prompt, not the constant. `build_system_prompt`
concatenates `MULDRO_SOUL_CORE` + the role prompt, and `_augment_system_blocks_for_inline`
appends `PRESENTER_VOICE` to the chat lead unconditionally — so three independently authored
strings meet in one message. Two `<rules>` blocks numbered 1-6 and 1-12 make "rule 3"
ambiguous.

The duplication originated in the SHARED half: `MULDRO_SOUL_CORE` opened with `<role>` and
`<rules>`, which collided with every role prompt that used the same tags. That is why the fix
is one rename of the shared block (`<identity>` / `<laws>`) rather than seven renames of the
role prompts.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from src.orchestrator.agent_invoker import AgentInvoker, _augment_system_blocks_for_inline
from src.orchestrator.agents import AGENTS
from src.orchestrator.lead_builder import _make_lead

# Tags that name a section's ROLE in the prompt. Two of the same tag = two answers to the
# same question. Content tags that legitimately repeat (e.g. <example>) are not listed.
STRUCTURAL_TAGS = ("role", "rules", "output_format", "workflow", "methodology")


def _composed_lead() -> str:
    lead = _make_lead(set(), False)
    blocks = AgentInvoker.build_system_prompt(SimpleNamespace(), lead)
    augmented = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)
    return "\n".join(b["text"] for b in augmented)


def _composed_agent(name: str) -> str:
    agent = AGENTS[name]
    blocks = AgentInvoker.build_system_prompt(SimpleNamespace(), agent, capability_summary="probe")
    return "\n".join(b["text"] for b in blocks)


def _duplicate_tags(text: str) -> list[str]:
    return [t for t in STRUCTURAL_TAGS if text.count(f"<{t}>") > 1]


def test_the_live_chat_lead_prompt_has_no_duplicate_structural_tag():
    text = _composed_lead()
    assert _duplicate_tags(text) == [], (
        f"the chat lead's composed prompt repeats {_duplicate_tags(text)}; "
        "the model gets two answers to the same structural question"
    )


@pytest.mark.parametrize("name", sorted(AGENTS))
def test_no_agent_composed_prompt_has_a_duplicate_structural_tag(name):
    text = _composed_agent(name)
    assert _duplicate_tags(text) == [], f"{name} repeats {_duplicate_tags(text)}"


def test_rule_numbering_is_unambiguous_in_the_lead_prompt():
    """Teeth on the actual symptom: two rule lists both starting at 1."""
    text = _composed_lead()
    blocks = re.findall(r"<rules>(.*?)</rules>", text, re.S)
    starts = [re.findall(r"^\s*(\d+)\.", b, re.M)[:1] for b in blocks]
    assert len([s for s in starts if s == ["1"]]) <= 1, (
        "more than one <rules> block starts numbering at 1, so a rule number is ambiguous"
    )
