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


def test_lede_splits_on_a_plain_blank_line():
    body = "First para.\n\nSecond para."
    assert lede_of(body) == "First para."


def test_lede_splits_on_a_crlf_paragraph_break():
    body = "First para.\r\n\r\nSecond para."
    assert lede_of(body) == "First para."


def test_lede_splits_on_a_blank_line_that_carries_a_space():
    body = "First para.\n \nSecond para."
    assert lede_of(body) == "First para."


def test_lede_splits_on_a_blank_line_that_carries_a_tab():
    body = "First para.\n\t\nSecond para."
    assert lede_of(body) == "First para."


def test_lede_does_not_treat_unicode_or_control_line_separators_as_line_breaks():
    # CommonMark recognizes only \n, \r\n and \r as line endings. str.splitlines()
    # additionally breaks on characters such as \u2028 (LINE SEPARATOR), \x0b
    # (vertical tab) and \x0c (form feed), none of which is a markdown line
    # break. The TypeScript mirror in unit-card.tsx splits only on "\n", so
    # using splitlines() here would silently diverge from it.
    assert lede_of("ls\u2028sep\n\nnext") == "ls\u2028sep"
    assert lede_of("ls\x0bsep\n\nnext") == "ls\x0bsep"
    assert lede_of("ls\x0csep\n\nnext") == "ls\x0csep"


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
