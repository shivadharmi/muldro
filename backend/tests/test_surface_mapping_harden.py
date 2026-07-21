"""build_surface_preview_from_plan: subtitle is plain text (no markdown).

Note: PlanStep has no `entities` field (fields: step_id, description, actor,
capability, input, depends_on, risk, user_context), so the entities-population
logic from the task spec is out of scope here — `entities=[]` is retained
in `build_surface_preview_from_plan` and no `_entities_from_plan` helper is
added.
"""

from src.contracts import PlanOutput, PlanStep
from src.services.surface_mapping import _plain_subtitle, build_surface_preview_from_plan


def test_plain_subtitle_strips_markdown_syntax():
    assert _plain_subtitle("## Heading\n\n**bold** text\n---\n") == "Heading bold text"
    assert _plain_subtitle("- bullet `code` item") == "bullet code item"
    assert _plain_subtitle("") == ""
    assert _plain_subtitle(None) is None


def test_summary_preview_subtitle_is_plain():
    plan = PlanOutput(
        goal="Read inbox",
        reasoning="### Found\n**3** urgent emails",
        steps=[PlanStep(description="read", capability="email.read")],
    )
    preview = build_surface_preview_from_plan(plan, "summary", "Summary", "")
    assert "**" not in (preview.subtitle or "")
    assert "#" not in (preview.subtitle or "")
    assert preview.subtitle == "Found 3 urgent emails"


def test_strips_markdown_links_keeping_label():
    assert (
        _plain_subtitle("See [thread](https://mail.google.com/x) for context")
        == "See thread for context"
    )


def test_strips_single_and_triple_asterisk_emphasis():
    assert _plain_subtitle("Found *2* urgent items") == "Found 2 urgent items"
    assert _plain_subtitle("***very important*** deadline") == "very important deadline"


def test_does_not_over_strip_normal_prose():
    # hyphenated words, em dash, and spaced multiplication survive intact.
    assert _plain_subtitle("well-known plan — 3 * 4 = 12") == "well-known plan — 3 * 4 = 12"
