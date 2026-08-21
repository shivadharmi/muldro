"""The body is one markdown field; the card shows its first paragraph.

Spec §2.3: the Glance renders paragraph 1, the Full renders the whole document,
so the Glance is a SEMANTIC prefix of the Full and the two cannot disagree.
This replaces seven disagreeing truncation rules with one budget per kind.
"""

import pytest

from src.view.body import LEDE_BUDGETS, BodyBudgetError, lede_of, validate_body

LONG_BODY = (
    "Three of your four active repos are effectively idle - "
    "muldrov1 is where everything is happening.\n"
    "\n"
    "## What moved\n"
    "\n"
    "muldrov1 took 63 commits this week, nearly all on the single-lead cutover branch.\n"
    "\n"
    "## What's stuck\n"
    "\n"
    "The PR on rules has been open eleven days with no review.\n"
)


def test_lede_is_the_first_paragraph():
    assert lede_of(LONG_BODY) == (
        "Three of your four active repos are effectively idle - "
        "muldrov1 is where everything is happening."
    )


def test_lede_joins_soft_wrapped_lines_in_one_paragraph():
    body = "Sarah is asking for a decision\nby Friday.\n\nMore detail here."
    assert lede_of(body) == "Sarah is asking for a decision by Friday."


def test_lede_skips_a_leading_heading():
    body = "# Repository activity\n\nThree repos moved this week."
    assert lede_of(body) == "Three repos moved this week."


def test_lede_of_single_paragraph_body_is_the_whole_body():
    body = "Sarah is asking for a decision by Friday."
    assert lede_of(body) == body


def test_lede_of_empty_body_is_empty():
    assert lede_of("") == ""
    assert lede_of("   \n\n  ") == ""


def test_validate_body_accepts_a_lede_within_budget():
    body = "Sarah is asking for a decision by Friday.\n\nMore detail."
    assert validate_body(body, "proposal") == body


def test_validate_body_rejects_an_overlong_lede():
    body = "x" * (LEDE_BUDGETS["proposal"] + 1)
    with pytest.raises(BodyBudgetError) as exc:
        validate_body(body, "proposal")
    # The message is returned to the model through the repair loop, so it must
    # say what to do, not merely that something is wrong.
    assert "140" in str(exc.value)
    assert "first paragraph" in str(exc.value)


def test_budgets_differ_by_kind():
    assert LEDE_BUDGETS["briefing"] < LEDE_BUDGETS["proposal"] < LEDE_BUDGETS["finding"]


def test_validate_body_rejects_an_unknown_kind():
    with pytest.raises(BodyBudgetError):
        validate_body("hello", "not_a_kind")


def test_validate_body_allows_an_unbounded_full_body():
    """Only the LEDE is budgeted. The rest of the document is not."""
    body = "Short lede.\n\n" + ("long detail " * 500)
    assert validate_body(body, "finding") == body
