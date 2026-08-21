"""The body's lede: paragraph one, within a budget the frame's kind sets.

A body may legitimately run long (a research finding, a briefing) and cannot
all fit on a card. The answer is not to cut it: the FIRST PARAGRAPH is a
complete, self-contained claim, the Glance renders that, and the Full renders
the whole document. The Glance is therefore a semantic prefix of the Full
rather than a character-count one, so they cannot disagree.

Code owns the budget; the model fills it. An overrun is a validation failure
returned through the existing typed-generation repair loop, never a truncation.
"""

from src.view.contracts import FrameKind

# Per-kind lede budget in characters. Starting points, not measurements —
# see docs/view-layer/spec.md §13 open question 1.
LEDE_BUDGETS: dict[str, int] = {
    "proposal": 140,  # one thread, one reason it needs you
    "finding": 180,  # research and synthesis are legitimately long
    "briefing": 90,  # sits in a list of peers; the lede has to scan
    "run": 120,  # what it did; the steps carry the detail
    "record": 120,
}


class BodyBudgetError(ValueError):
    """The body's first paragraph does not fit its kind's budget."""


def lede_of(body: str) -> str:
    """Return the first prose paragraph of a markdown body, soft-wraps joined.

    Leading ATX headings are skipped: a heading is a label for what follows,
    not the claim itself. Returns "" for an empty or heading-only body.
    """
    for block in body.split("\n\n"):
        lines = [line.strip() for line in block.strip().splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        if lines:
            return " ".join(lines)
    return ""


def validate_body(body: str, kind: FrameKind | str) -> str:
    """Return `body` unchanged, or raise BodyBudgetError naming the fix.

    Only the lede is budgeted. The remainder of the document is unbounded.
    """
    budget = LEDE_BUDGETS.get(str(kind))
    if budget is None:
        raise BodyBudgetError(
            f"unknown frame kind {kind!r}; expected one of {sorted(LEDE_BUDGETS)}"
        )
    lede = lede_of(body)
    if len(lede) > budget:
        raise BodyBudgetError(
            f"the body's first paragraph is {len(lede)} characters; a {kind} "
            f"allows {budget}. Rewrite the first paragraph as a complete, "
            f"self-contained claim within {budget} characters and move the "
            f"detail into later paragraphs."
        )
    return body
