"""build_briefing_preview: one structured SurfacePreview from a Briefing row.

Guards the single-source-of-truth helper that both the REST rebuild
(_build_briefing_surface) and the live push (push_briefing_surface) call, so a
briefing card is never a markdown blob and never duplicated.
"""

from types import SimpleNamespace

from src.services.surface_mapping import build_briefing_preview


def _briefing(**kw):
    base = dict(
        briefing_id="brief_01",
        headline="Your Tuesday",
        top_priorities=[{"title": "Pay LIC premium"}, {"title": "Reply to investor"}],
        recommended_actions=[{"title": "Draft reply"}],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_populates_items_and_metrics_from_briefing():
    preview = build_briefing_preview(_briefing())
    assert preview.title == "Your Tuesday"
    assert preview.items == ["Pay LIC premium", "Reply to investor"]
    labels = {m.label: m.value for m in preview.metrics}
    assert labels == {"Priorities": "2", "Actions": "1"}
    assert preview.tags == ["briefing"]
    # subtitle is the first priority (plain), never a markdown blob.
    assert preview.subtitle == "Pay LIC premium"


def test_missing_headline_falls_back_to_daily_briefing():
    preview = build_briefing_preview(
        _briefing(headline=None, top_priorities=[], recommended_actions=[])
    )
    assert preview.title == "Daily Briefing"
    assert preview.items == []
    assert preview.subtitle is None
    assert {m.label: m.value for m in preview.metrics} == {"Priorities": "0", "Actions": "0"}


def test_priority_strings_and_dicts_both_supported():
    preview = build_briefing_preview(
        _briefing(top_priorities=["bare string", {"title": "dict one"}])
    )
    assert preview.items == ["bare string", "dict one"]
