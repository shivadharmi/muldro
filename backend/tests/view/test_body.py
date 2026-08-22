"""The body is one markdown field; the card shows its first paragraph.

The Glance renders paragraph 1 and the Full renders the whole document, so the
Glance is a SEMANTIC prefix of the Full and the two cannot disagree.
This replaces seven disagreeing truncation rules with one budget per kind.

The lede cases live in `fixtures/lede_corpus.json` rather than in this file:
`lede_of` has a TypeScript mirror (`ledeOf` in unit-card.tsx) whose own test
reads the SAME file, and two independently hand-written corpora is precisely
how the pair came to disagree on Unicode whitespace.
"""

import json
from pathlib import Path
from typing import get_args

import pytest

from src.view.body import LEDE_BUDGETS, BodyBudgetError, lede_of, validate_body
from src.view.contracts import FrameKind

LEDE_CORPUS = Path(__file__).parent / "fixtures" / "lede_corpus.json"
_CASES = json.loads(LEDE_CORPUS.read_text(encoding="utf-8"))["cases"]


def test_the_shared_corpus_is_actually_loaded():
    """A fixture that silently failed to load would make the pinning vacuous."""
    assert len(_CASES) > 10
    assert {"name", "body", "lede"} <= set(_CASES[0])


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_lede_of_matches_the_shared_corpus(case):
    """The differential pin across the two implementations.

    `frontend/src/components/workspace/unit-card.test.tsx` asserts `ledeOf`
    against these same cases. Nothing else fails when one implementation
    changes alone - and when they disagree, the card renders as prose a
    heading the backend budgeted as a heading, which is the Glance and the
    Full drifting apart.
    """
    assert lede_of(case["body"]) == case["lede"]


def test_frame_kinds_and_lede_budgets_are_the_same_set():
    """FrameKind is enumerated in five places and pinned in none.

    Adding "alert" to the Literal without adding a budget makes
    `validate_body(body, "alert")` raise BodyBudgetError for every alert -
    from a function whose contract is "return `body` unchanged or name the
    fix". This closes the BACKEND half only. Three frontend enumerations
    remain unpinned - `FrameKind` in `frontend/src/lib/view/unit.ts` and
    both `KIND_LABELS` and `kindStyle` in `design-tokens.ts` - and only the
    transport work (one generated contract) can close those properly; from
    here, a Python test can do no better than duplicate them.
    """
    assert set(get_args(FrameKind)) == set(LEDE_BUDGETS)


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
