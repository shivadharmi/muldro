"""Tests for the message-promotion gate."""

from src.services.message_promotion import (
    count_sections,
    has_structural_component,
    should_promote_to_workspace,
)


def _text(text: str) -> dict:
    return {"type": "Text", "id": "t", "properties": {"text": text}, "children": []}


def _card(children: list[dict]) -> dict:
    return {"type": "Card", "id": "c", "properties": {}, "children": children}


def _table() -> dict:
    return {"type": "Table", "id": "tbl", "properties": {}, "children": []}


def test_plain_text_reply_does_not_promote():
    # A single Text inside a Card is a wrapper, not a section.
    children = [_card([_text("Sure, I can help.")])]
    assert not should_promote_to_workspace(children)


def test_table_component_triggers_promotion():
    children = [_card([_table()])]
    assert has_structural_component(children) is True
    assert should_promote_to_workspace(children) is True


def test_multi_section_layout_triggers_promotion():
    # Two cards, each with 2+ children → 2 sections → promote
    children = [
        _card([_text("summary"), _text("details")]),
        _card([_text("plan"), _text("steps")]),
    ]
    assert count_sections(children) == 2
    assert should_promote_to_workspace(children) is True


def test_single_section_without_structural_does_not_promote():
    children = [_card([_text("one"), _text("two")])]
    assert count_sections(children) == 1
    assert should_promote_to_workspace(children) is False


def test_explicit_flag_overrides_heuristic():
    children = [_card([_text("just a line")])]
    assert should_promote_to_workspace(children, explicit_flag=True) is True


def test_nested_structural_component_triggers_promotion():
    # Table buried deep in nested layout containers → still structural → promote
    children = [
        {
            "type": "Card",
            "id": "col",
            "properties": {},
            "children": [
                {
                    "type": "Row",
                    "id": "row",
                    "properties": {},
                    "children": [{"type": "Table", "id": "tbl", "properties": {}, "children": []}],
                }
            ],
        }
    ]
    assert should_promote_to_workspace(children) is True


def test_object_attribute_access_works():
    """The gate accepts typed component objects, not just dicts."""

    class FakeComponent:
        def __init__(self, type_: str, children=None):
            self.type = type_
            self.children = children or []

    children = [FakeComponent("Card", [FakeComponent("Table")])]
    assert should_promote_to_workspace(children) is True
