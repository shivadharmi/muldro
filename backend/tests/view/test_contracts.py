"""The Frame is code-authored and plain-text.

Invariant 2 (docs/view-layer/spec.md §10): frame.headline is plain text and is
never passed to a markdown renderer. The type refuses markdown syntax so an
email subject cannot arrive here and become a live link in muldro's voice.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.view.contracts import Frame, Quote, Unit


def _frame(**overrides) -> Frame:
    defaults = dict(
        key="gmail:email_thread:t_123",
        kind="proposal",
        status="needs_you",
        headline="Sarah Chen - Series A term sheet",
        source="gmail",
        occurred_at=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
        importance=0.8,
    )
    defaults.update(overrides)
    return Frame(**defaults)


def test_frame_defaults_to_no_affordances():
    assert _frame().affordances == []


def test_an_affordance_carries_a_capability_and_a_code_authored_label():
    from src.view.contracts import Affordance

    a = Affordance(capability="email.send", label="Draft a reply", variant="primary")
    assert a.capability == "email.send"
    assert a.label == "Draft a reply"


def test_frame_accepts_plain_headline():
    assert _frame().headline == "Sarah Chen - Series A term sheet"


@pytest.mark.parametrize(
    "headline",
    [
        "**URGENT** verify your account",
        "[Verify your account](https://phish.example)",
        "Check https://phish.example now",
        "# Heading",
        "`code`",
    ],
)
def test_frame_rejects_markdown_and_bare_urls_in_headline(headline):
    with pytest.raises(ValidationError):
        _frame(headline=headline)


def test_frame_rejects_blank_headline():
    with pytest.raises(ValidationError):
        _frame(headline="   ")


def test_frame_is_frozen():
    f = _frame()
    with pytest.raises(ValidationError):
        f.headline = "changed"


def test_unit_defaults_to_no_quotes():
    u = Unit(frame=_frame(), body="Sarah is asking for a decision by Friday.")
    assert u.quotes == []


def test_quote_carries_attribution():
    q = Quote(
        text="Can you get back to me by Friday?",
        who="Sarah Chen <sarah@example.com>",
        when=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
    )
    assert q.who == "Sarah Chen <sarah@example.com>"
