"""Step 9 P0 — characterization guardrails for the SHARED A2UI render payload.

These tests PIN the CURRENT (pre-cleanup) behavior of the A2UI declarative
surface layer so that Step 9's later phases are fenced by green tests:

  * P1 will DELETE the 13 never-produced ComponentType values + 5 dead surface
    kinds. The count tripwire below (29 → 16) fails loudly the moment the enum
    changes, forcing whoever edits it to update this snapshot deliberately.
  * P2 will ADD a Markdown component (16 → 17) and rewire the narrative builders
    (briefing / insight) to emit it. The narrative-builder characterizations pin
    what those builders emit TODAY so the P2 rewire is a visible, reviewed diff.

Nothing here asserts desired end-state — every assertion snapshots what the code
does right now. They all PASS on write.
"""

from unittest.mock import AsyncMock, patch

from src.services.surface_detail_builders.briefing import build_briefing_priorities
from src.services.surface_detail_builders.insight import build_insight_signal
from src.ui import renderer as r
from src.ui.contracts import ComponentType

# The 17 ComponentType values that renderer.py / narrative builders actually
# PRODUCE. P1 deleted the 13 dead types; P2 added Markdown (the 17th) — the
# narrative builders now emit it for prose bodies.
LIVE_COMPONENT_TYPES = frozenset(
    {
        "Text",
        "Badge",
        "Row",
        "Card",
        "Metric",
        "Button",
        "Alert",
        "List",
        "Table",
        "Timeline",
        "MemoryCard",
        "Divider",
        "CodeBlock",
        "Progress",
        "EntityCard",
        "ExecutionTrace",
        "Markdown",
    }
)


def test_live_component_types_all_exist_in_enum():
    """After P1, ComponentType is EXACTLY the live set (no dead types remain)."""
    enum_values = {ct.value for ct in ComponentType}
    assert LIVE_COMPONENT_TYPES == enum_values, (
        f"ComponentType drifted from the live set: "
        f"missing={LIVE_COMPONENT_TYPES - enum_values}, extra={enum_values - LIVE_COMPONENT_TYPES}"
    )


def test_component_type_count_tripwire():
    """TRIPWIRE: ComponentType currently has exactly 17 values.

    Do NOT relax this to make an unrelated change pass — a drift here means the
    enum changed. This number is edited DELIBERATELY by later Step 9 phases:
      * P1 deleted the 13 dead types   -> count was 16
      * P2 added the Markdown component -> count is now 17
    When you make those edits, change the literal below and this comment to match.
    """
    assert len({ct.value for ct in ComponentType}) == 17


def test_live_builders_emit_expected_component_types():
    """Each live builder returns the exact `.type` string it emits today.

    Read against renderer.py source — these are the ACTUAL return types, not
    aspirational ones. If a P1/P2 rename changes a builder's emitted type, this
    fails and forces the snapshot to be updated intentionally.
    """
    builder_to_type = {
        "text": r.text("t", "hi").type,
        "heading": r.heading("h", "hi").type,
        "caption": r.caption("c", "hi").type,
        "badge": r.badge("b", "lbl").type,
        "alert": r.alert("a", "msg").type,
        "code_block": r.code_block("cb", "x = 1").type,
        "card": r.card("cd", []).type,
        "row": r.row("rw", []).type,
        "list_component": r.list_component("l", []).type,
        "divider": r.divider("d").type,
        "table": r.table("tb", [], []).type,
        "timeline": r.timeline("tl", []).type,
        "metric": r.metric("m", "Relevance", 1).type,
        "progress": r.progress("p", 50).type,
        "entity_card": r.entity_card("ec", "Acme", "org").type,
        "memory_card": r.memory_card("mc", "fact", "preference").type,
        "execution_trace": r.execution_trace("et", []).type,
        "button": r.button("bt", "Run").type,
    }

    expected = {
        # NOTE: heading and caption are NOT distinct component types — both emit
        # type="Text" and are distinguished only by the `variant` property. This
        # matters for P2: a Markdown component is a genuinely new type, whereas
        # caption/heading are just Text variants.
        "text": "Text",
        "heading": "Text",
        "caption": "Text",
        "badge": "Badge",
        "alert": "Alert",
        "code_block": "CodeBlock",
        "card": "Card",
        "row": "Row",
        "list_component": "List",
        "divider": "Divider",
        "table": "Table",
        "timeline": "Timeline",
        "metric": "Metric",
        "progress": "Progress",
        "entity_card": "EntityCard",
        "memory_card": "MemoryCard",
        "execution_trace": "ExecutionTrace",
        "button": "Button",
    }
    assert builder_to_type == expected


def test_caption_and_heading_are_text_variants_not_distinct_types():
    """Pin that caption/heading are Text with a `variant`, not their own types."""
    caption = r.caption("c", "small note")
    heading = r.heading("h", "Big Title")
    body = r.text("t", "plain body")

    assert caption.type == "Text"
    assert caption.properties["variant"] == "caption"
    assert heading.type == "Text"
    assert heading.properties["variant"] == "heading"
    assert body.type == "Text"
    assert body.properties["variant"] == "body"


def test_markdown_builder_emits_new_markdown_type_with_content():
    """P2: r.markdown is a genuinely NEW component type (not a Text variant).

    It carries the raw GitHub-flavored markdown source in properties['content']
    so the frontend can render paragraph/list/emphasis structure via
    react-markdown instead of the flattened single-line Text it replaces.
    """
    comp = r.markdown("id", "# H\n- a\n- b")

    assert comp.type == "Markdown"
    assert comp.properties["content"] == "# H\n- a\n- b"
    assert "Markdown" in {ct.value for ct in ComponentType}


async def test_build_insight_signal_emits_badge_text_metric():
    """P2 rewire tripwire: characterize the node types build_insight_signal emits.

    Today the signal tab is a flat list of Badge (source), Text (summary),
    Metric (relevance), Text (reasoning via caption). P2 may rewire the prose
    parts to Markdown — this pins the current shape so that change is visible.
    build_insight_signal reads only surface.payload (no DB), so db is unused.
    """

    class _FakeSurface:
        payload = {
            "insight_data": {
                "signal_source": "gmail",
                "signal_summary": "New investor email requesting the deck",
                "relevance_score": 0.92,
                "relevance_reasoning": "Matches the active fundraising goal",
            }
        }

    resp = await build_insight_signal(None, _FakeSurface())
    children = resp.sections[0].children
    types = [c.type for c in children]

    assert types == ["Badge", "Text", "Metric", "Text"]
    assert set(types) <= LIVE_COMPONENT_TYPES


async def test_build_briefing_priorities_emits_text_and_divider():
    """P2 rewire tripwire: characterize build_briefing_priorities node types.

    Each priority emits a Text (title) + a caption-Text (why); a Divider is
    inserted between consecutive priorities. Patch _resolve_briefing to avoid a
    DB round-trip and pin the emitted component types.
    """

    class _FakeBriefing:
        top_priorities = [
            {"title": "Ship the investor deck", "why": "Investor is waiting"},
            {"title": "Reply to Jane", "why": "Time-sensitive intro"},
        ]

    with patch(
        "src.services.surface_detail_builders.briefing._resolve_briefing",
        new=AsyncMock(return_value=(_FakeBriefing(), True)),
    ):
        resp = await build_briefing_priorities(None, object())

    children = resp.sections[0].children
    types = [c.type for c in children]

    # priority 0: title(Text) + why(Text) + divider(Divider); priority 1: title + why
    assert types == ["Text", "Text", "Divider", "Text", "Text"]
    assert set(types) == {"Text", "Divider"}
    assert set(types) <= LIVE_COMPONENT_TYPES
