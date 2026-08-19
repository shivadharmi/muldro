"""PRESENTER_PROMPT content pin + dead-schema guard.

``PRESENTER_VOICE`` (the reusable formatting + surface-emission fragment) is extracted
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
The companion ``test_presenter_voice_has_no_dead_schema_tokens`` gives that prune teeth so
a drift re-introducing a dead kind/type fails CI.

It was re-baselined a second time by the Muldro product rebrand, which renamed the product
throughout. The guarded diff was exclusively that name inside the ``<role>`` line ("the
Presenter agent in <product>"); ``PRESENTER_VOICE`` itself was byte-identical across the
rebrand, so the reusable voice fragment carries no rebrand delta.

It was re-baselined a third time when the surface-kind guidance was corrected in **both**
directions. ``message`` — the kind defined for Presenter-authored content, with its own
promotion path in ``surface_pusher`` — was never offered in the kinds table, so the model was
never told its own default existed; it was added. And the "do not use" list forbade only
``approval`` and ``proactive_insight``, leaving ``run``, ``prepared_work`` and the legacy
``plan`` emittable, since ``SurfaceSpec.kind`` validates against the whole Literal and forbids
nothing the prompt does not; all three were added. ``prepared_work`` in particular, because
settled decision D2 makes that review queue the ONLY place an action staged with no human
present can be acted on — an agent-authored second one would split it. The companion
``test_surface_kind_guidance`` parses the table rows and the bullet list as structure and
asserts set relations against the Literal in both directions, so this correction cannot drift
back silently.

To re-baseline intentionally, recompute with:
    uv run python -c "from src.orchestrator.prompts import PRESENTER_PROMPT; \\
        import hashlib; print(hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest())"
"""

import hashlib

from src.orchestrator.prompts import PRESENTER_PROMPT, PRESENTER_VOICE

# Golden sha256 of PRESENTER_PROMPT. Re-baselined in Step-9 P1 (dead-kind/type prune),
# again for the Muldro product rebrand (name substitution only), and again for the
# surface-kind guidance correction (`message` offered; `run`/`prepared_work`/`plan` forbidden).
_PRESENTER_PROMPT_GOLDEN_SHA256 = "cc21f92c4c51bc8fc791672a7af8e4ee3735a619730eeb36d63e1b81d98827b6"


def test_presenter_prompt_matches_golden_hash():
    """PRESENTER_PROMPT bytes must match the pinned golden hash (guards unintended edits)."""
    actual = hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest()
    assert actual == _PRESENTER_PROMPT_GOLDEN_SHA256, (
        "PRESENTER_PROMPT bytes changed — if this was an intentional content edit, "
        "re-baseline the golden hash (see module docstring). "
        f"Expected {_PRESENTER_PROMPT_GOLDEN_SHA256}, got {actual}"
    )


def test_presenter_voice_has_no_dead_schema_tokens():
    """PRESENTER_VOICE must not advertise pruned surface-kinds or component-types.

    A one-line reintroduction of any dead kind/type into the prompt must fail here.
    Tokenization is deliberately precise so live component types ``Table``/``Timeline``
    (whose same-named *kinds* were deleted) do not trigger false hits.
    """
    # Dead surface KINDS — matched via their kinds-table cell / worked-example form so
    # the live component types Table/Timeline are not falsely flagged.
    dead_kind_tokens = [
        "| checklist ",
        "| comparison ",
        "| timeline ",
        "| activity ",
        '"kind": "table"',
    ]
    for token in dead_kind_tokens:
        assert token not in PRESENTER_VOICE, f"dead surface-kind token still in prompt: {token!r}"

    # Dead COMPONENT TYPES — deleted from the ComponentType enum.
    dead_component_tokens = ["DataGrid", "StatusIndicator", "KanbanBoard", "Calendar"]
    for token in dead_component_tokens:
        assert token not in PRESENTER_VOICE, f"dead component-type token still in prompt: {token!r}"

    # The layout-container line must no longer list the deleted ``Column`` container.
    assert "Column" not in PRESENTER_VOICE, "deleted layout container 'Column' still in prompt"


def test_presenter_voice_is_substring_of_presenter_prompt():
    """The extracted fragment must be the exact contiguous block still embedded in
    PRESENTER_PROMPT (rules + surface-generation guidance)."""
    assert PRESENTER_VOICE in PRESENTER_PROMPT
    # The fragment is the reusable voice, NOT the Presenter-specific role or examples.
    assert PRESENTER_VOICE.startswith("<rules>")
    assert PRESENTER_VOICE.endswith("</surface_generation>")
    assert "<role>" not in PRESENTER_VOICE
    assert "<examples>" not in PRESENTER_VOICE
