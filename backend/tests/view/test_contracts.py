"""The Frame is code-authored and plain-text.

frame.headline is plain text and is never passed to a markdown renderer. The
type refuses markdown syntax so an email subject cannot arrive here and become
a live link in muldro's voice.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.view.contracts import Affordance, Frame, Quote, Unit


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
    assert _frame().affordances == ()


def test_an_affordance_carries_a_capability_and_a_code_authored_label():
    a = Affordance(capability="email.send", label="Draft a reply", variant="primary")
    assert a.capability == "email.send"
    assert a.label == "Draft a reply"


def test_frame_accepts_plain_headline():
    assert _frame().headline == "Sarah Chen - Series A term sheet"


@pytest.mark.parametrize(
    "headline",
    [
        pytest.param("**URGENT** verify your account", id="bold"),
        pytest.param("[Verify your account](https://phish.example)", id="link"),
        pytest.param("Check https://phish.example now", id="bare-https-url"),
        pytest.param("# Heading", id="heading"),
        pytest.param("`code`", id="code-span"),
        pytest.param("www.phish.example verify", id="gfm-www-autolink"),
        pytest.param("Verify at security@phish.example", id="gfm-email-autolink"),
        pytest.param("<mailto:a@phish.example>", id="commonmark-protocol-autolink"),
        pytest.param("*URGENT* now", id="single-asterisk-emphasis"),
        pytest.param("x ~~old~~ new", id="strikethrough"),
        pytest.param("Two\nlines", id="embedded-newline"),
        pytest.param("Bad\x07bell", id="c0-control-character"),
        pytest.param("Legit\u202ereversed", id="bidi-rtl-override"),
    ],
)
def test_frame_rejects_headlines_that_could_render_as_markdown_or_spoofed_text(headline):
    """No headline may become a link, emphasis, a heading, strikethrough, or
    plain-text-spoofed via control/bidi characters — this is the class of
    input the design refuses, not an enumeration of one regex branch each."""
    with pytest.raises(ValidationError):
        _frame(headline=headline)


def test_frame_rejects_blank_headline():
    with pytest.raises(ValidationError):
        _frame(headline="   ")


def test_frame_is_frozen():
    f = _frame(affordances=[Affordance(capability="email.send", label="Draft a reply")])
    with pytest.raises(ValidationError):
        f.headline = "changed"
    # affordances is code-authored and un-mintable; a frozen model whose
    # collection field is still a mutable list would let a caller append to
    # it in place without ever tripping pydantic's frozen check.
    with pytest.raises(AttributeError):
        f.affordances.append(Affordance(capability="email.archive", label="Archive"))


def test_frame_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        _frame(unexpected="x")


def test_affordance_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Affordance(capability="email.send", label="Draft a reply", unexpected="x")


def test_quote_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Quote(
            text="Can you get back to me by Friday?",
            who="Sarah Chen <sarah@example.com>",
            when=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
            unexpected="x",
        )


def test_unit_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        Unit(frame=_frame(), body="hi", unexpected="x")


def test_unit_defaults_to_no_quotes():
    u = Unit(frame=_frame(), body="Sarah is asking for a decision by Friday.")
    assert u.quotes == ()


def test_quote_carries_attribution():
    q = Quote(
        text="Can you get back to me by Friday?",
        who="Sarah Chen <sarah@example.com>",
        when=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
    )
    assert q.who == "Sarah Chen <sarah@example.com>"


def test_quote_text_preserves_markdown_verbatim():
    """Quotes are deliberately NOT sanitized — neutralizing would misrepresent
    what the sender wrote. Safety comes from the renderer never passing a
    quote to a markdown renderer, not from Quote itself stripping anything."""
    raw = "**URGENT** click https://phish.example now"
    q = Quote(
        text=raw,
        who="Sarah Chen <sarah@example.com>",
        when=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
    )
    assert q.text == raw
