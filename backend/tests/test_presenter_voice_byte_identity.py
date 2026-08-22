"""PRESENTER_PROMPT content pin.

``PRESENTER_VOICE`` (the reusable formatting fragment) is extracted
from ``PRESENTER_PROMPT`` so the deep runtime can append it as an inline-format
augmentation. This golden-hash test pins the exact bytes of ``PRESENTER_PROMPT`` so
unintended edits to the prompt are caught.

History: the hash originally pinned the Step-7B1-P4 *byte-neutral* extraction of
PRESENTER_VOICE (the extraction was required to change zero bytes). It was **re-baselined
in Step-9 P1** when the dead surface-kinds (``checklist, comparison, timeline, table,
activity``) and dead component-types (``DataGrid``, ``StatusIndicator``, ``Column``) were
pruned from PRESENTER_VOICE in lockstep with their deletion from the A2UI schema — the
prompt was still advertising kinds that ``SurfaceSpec.kind`` (a strict ``SurfaceKind``
Literal) now rejects, which silently dropped chat-path workspace surfaces. The hash no
longer asserts historical byte-neutrality; it pins the *current* pruned content forward.
A companion ``test_presenter_voice_has_no_dead_schema_tokens`` gave that prune teeth, until
the sixth re-baseline below removed the prose it searched.

It was re-baselined a second time by the Muldro product rebrand, which renamed the product
throughout. The guarded diff was exclusively that name inside the ``<role>`` line ("the
Presenter agent in <product>"); ``PRESENTER_VOICE`` itself was byte-identical across the
rebrand, so the reusable voice fragment carries no rebrand delta.

It was re-baselined a third time when the surface-kind guidance was corrected in **both**
directions. ``message`` — the kind defined for Presenter-authored content, which had its own
promotion path at the time — was never offered in the kinds table, so the model was never
told its own default existed; it was added. And the "do not use" list forbade only
``approval`` and ``proactive_insight``, leaving ``run``, ``prepared_work`` and the legacy
``plan`` emittable, since ``SurfaceSpec.kind`` validates against the whole Literal and forbids
nothing the prompt does not; all three were added. ``prepared_work`` in particular, because
settled decision D2 makes that review queue the ONLY place an action staged with no human
present can be acted on — an agent-authored second one would split it. The companion
``test_surface_kind_guidance`` parses the table rows and the bullet list as structure and
asserts set relations against the Literal in both directions, so this correction cannot drift
back silently.

It was re-baselined a fourth time when ``TableProperties.rows`` became positional ``cells``.
The prompt was the only *other* producer of Table components — the lead authors them directly
in a ```json:surface_data``` block — and it taught row-keyed-by-column-key, a shape the closed
model now rejects. That is not a cosmetic drift: one bad Table makes ``extract_surface_data``
return ``None`` for the WHOLE payload, so every section of the surface is dropped silently.
The companion ``TestPresenterVoiceExampleMatchesTheSchema`` in ``test_surface_spec.py`` now
runs the prompt's own worked example through the real parser, so prose and schema can no
longer disagree without CI saying so.

It was re-baselined a fifth time when ``EntityCardProperties.attributes`` became a closed list
of ``{"key", "value"}`` pairs. The prompt taught ``"attributes"?: {}`` — an open map, which is
the exact shape the closed model now rejects and the reason the component schema could not be
handed to a provider for structured-output enforcement in the first place. An EntityCard was
added to the worked example at the same time, so the parser test above now covers the new
shape rather than only asserting the prose was edited.

It was re-baselined a sixth time when the whole ``<surface_generation>`` block was deleted.
The A2UI taxonomy it recited — the kinds table, the ```json:surface``` and
```json:surface_data``` worked examples, the component list with each type's required
properties, and the list-of-dict rules — moved out of prose and into a typed tool input
schema, where a provider could enforce it instead of a model having to remember it. What
replaced it was a short ``<surfaces>`` block carrying only WHEN to surface: judgement a
schema cannot express. The dead-schema-token guard was deleted outright at the same time,
because the prompt no longer named a single kind or component type for a drift to
re-introduce.

It was re-baselined a seventh time when model-authored UI was removed entirely. There is no
longer a tool for the model to draw a card with, so the ``<surfaces>`` block and the two
surface title/subtitle length rules had nothing left to govern and were deleted. What
remains is the voice alone: how to speak, not what to draw.

To re-baseline intentionally, recompute with:
    uv run python -c "from src.orchestrator.prompts import PRESENTER_PROMPT; \\
        import hashlib; print(hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest())"
"""

import hashlib

from src.orchestrator.prompts import PRESENTER_PROMPT, PRESENTER_VOICE

# Golden sha256 of PRESENTER_PROMPT. Re-baselined in Step-9 P1 (dead-kind/type prune),
# again for the Muldro product rebrand (name substitution only), again for the surface-kind
# guidance correction (`message` offered; `run`/`prepared_work`/`plan` forbidden), again
# for the positional Table.rows shape (`{"cells": [...]}` replacing rows keyed by column key),
# again for EntityCard.attributes becoming a list of `{"key", "value"}` pairs, again for the
# deletion of `<surface_generation>`, and again for the removal of model-authored UI — with
# no tool left to draw a card, the `<surfaces>` block and its two length rules governed
# nothing and were deleted.
_PRESENTER_PROMPT_GOLDEN_SHA256 = "6ecd297907da9bf15d162178a95d403434ab1e7665e906ce9fd79049f8c9a7cd"


def test_presenter_prompt_matches_golden_hash():
    """PRESENTER_PROMPT bytes must match the pinned golden hash (guards unintended edits)."""
    actual = hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest()
    assert actual == _PRESENTER_PROMPT_GOLDEN_SHA256, (
        "PRESENTER_PROMPT bytes changed — if this was an intentional content edit, "
        "re-baseline the golden hash (see module docstring). "
        f"Expected {_PRESENTER_PROMPT_GOLDEN_SHA256}, got {actual}"
    )


def test_presenter_voice_is_substring_of_presenter_prompt():
    """The extracted fragment must be the exact contiguous block still embedded in
    PRESENTER_PROMPT (the formatting rules)."""
    assert PRESENTER_VOICE in PRESENTER_PROMPT
    # The fragment is the reusable voice, NOT the Presenter-specific role or examples.
    assert PRESENTER_VOICE.startswith("<rules>")
    assert PRESENTER_VOICE.endswith("</rules>")
    assert "<role>" not in PRESENTER_VOICE
    assert "<examples>" not in PRESENTER_VOICE
