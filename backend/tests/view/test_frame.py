"""frame.key is deterministic and names a durable thing, not an occurrence.

Spec §3 / §10 invariant 6. Identity must be deterministic or dedup is not
dedup: an inferred key lets two runs of one pipeline mint two keys for one
thing, which is why three polls of one inbox produced three "New activity"
cards.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.view.contracts import _MARKDOWN_IN_HEADLINE, MAX_HEADLINE_CHARS
from src.view.frame import frame_for_event


def _event(**overrides):
    """A NormalizedEvent-shaped stand-in.

    frame_for_event reads attributes only, so a namespace is sufficient and
    keeps this a pure unit test with no DB.
    """
    defaults = dict(
        source="gmail",
        entity_type="email_thread",
        entity_id="t_abc123",
        event_type="email_received",
        title="Series A term sheet",
        occurred_at=datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc),
        actor_entities={"name": "Sarah Chen"},
        importance_score=0.8,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_key_is_source_entity_type_entity_id():
    assert frame_for_event(_event()).key == "gmail:email_thread:t_abc123"


def test_key_is_deterministic_across_calls():
    assert frame_for_event(_event()).key == frame_for_event(_event()).key


def test_two_events_on_one_thread_share_a_key():
    """This is the fix for three identical 'New activity' cards."""
    first = frame_for_event(_event(title="Series A term sheet"))
    second = frame_for_event(_event(title="Re: Series A term sheet"))
    assert first.key == second.key


def test_different_threads_get_different_keys():
    a = frame_for_event(_event(entity_id="t_aaa"))
    b = frame_for_event(_event(entity_id="t_bbb"))
    assert a.key != b.key


def test_headline_names_the_counterparty_and_the_subject():
    frame = frame_for_event(_event())
    assert frame.headline == "Sarah Chen - Series A term sheet"


def test_headline_falls_back_to_the_subject_when_the_actor_is_unknown():
    frame = frame_for_event(_event(actor_entities=None))
    assert frame.headline == "Series A term sheet"


def test_markdown_in_a_subject_is_neutralized_not_rejected():
    """A real inbound subject must still produce a card - just an inert one.

    Refusing outright would mean a phishing subject silently suppresses its
    own card, which hides the message from the founder entirely.
    """
    frame = frame_for_event(
        _event(title="**URGENT** [Verify](https://phish.example)", actor_entities=None)
    )
    assert "**" not in frame.headline
    assert "](" not in frame.headline
    assert "https://" not in frame.headline
    assert frame.headline  # non-empty: the card still exists


def test_headline_falls_back_when_the_subject_is_entirely_markdown():
    frame = frame_for_event(_event(title="**", actor_entities=None))
    assert frame.headline == "gmail email_thread"


def test_a_model_authored_importance_score_on_the_event_never_reaches_the_frame():
    """Spec §10 invariants 4 and 8. The event's score is LLM-authored.

    `NormalizedEvent.importance_score` is written straight from LLM JSON by a
    prompt that reads the event's title and summary - the attacker-controlled
    subject and body. Carrying it onto a Frame would let external text raise
    its own rank on a class documented as carrying no model-authored field.
    """
    assert frame_for_event(_event(importance_score=0.9)).importance == 0.0


def test_importance_defaults_to_zero_when_no_caller_supplies_one():
    assert frame_for_event(_event()).importance == 0.0


def test_importance_is_supplied_by_the_caller():
    assert frame_for_event(_event(), importance=0.7).importance == 0.7


def test_kind_status_and_importance_are_supplied_by_the_caller_not_the_event():
    frame = frame_for_event(_event(), kind="finding", status="new", importance=0.4)
    assert frame.kind == "finding"
    assert frame.status == "new"
    assert frame.importance == 0.4


# --- actor_entities is a LIST in production -------------------------------
#
# NormalizedEvent annotates `actor_entities: Mapped[dict | None]`, but that
# annotation has never matched the data: EventProcessor stores
# `[raw.actor] if raw.actor else None` at both writer sites, and every other
# consumer (event_correlator, presenter, event_processor itself) iterates or
# subscripts it. A dict-only reader would be dead code against real rows and
# every production headline would silently be subject-only.


def test_actor_name_is_read_from_the_list_shape_production_writes():
    frame = frame_for_event(_event(actor_entities=[{"name": "Sarah Chen"}]))
    assert frame.headline == "Sarah Chen - Series A term sheet"


def test_actor_name_falls_back_to_canonical_name_in_the_list_shape():
    frame = frame_for_event(_event(actor_entities=[{"canonical_name": "Sarah Chen"}]))
    assert frame.headline == "Sarah Chen - Series A term sheet"


def test_actor_name_takes_the_first_usable_entry():
    frame = frame_for_event(
        _event(actor_entities=[{"email": "s@example.com"}, {"name": "Sarah Chen"}])
    )
    assert frame.headline == "Sarah Chen - Series A term sheet"


def test_actor_list_with_no_usable_name_falls_back_to_the_subject():
    frame = frame_for_event(_event(actor_entities=[{"email": "s@example.com"}]))
    assert frame.headline == "Series A term sheet"


def test_empty_actor_list_falls_back_to_the_subject():
    assert frame_for_event(_event(actor_entities=[])).headline == "Series A term sheet"


def test_junk_in_the_actor_list_does_not_raise():
    frame = frame_for_event(_event(actor_entities=["Sarah Chen", None, 7]))
    assert frame.headline == "Series A term sheet"


# --- importance is clamped, never raised ----------------------------------
#
# Frame.importance is ge=0.0 le=1.0 and the caller supplies it - eventually
# §6's ranker. An out-of-range value must not raise here: a ValidationError
# inside frame_for_event means the card silently never exists, which is the
# outcome the design rejected when it chose to neutralize a phishing subject
# rather than refuse it. A future ranker bug should degrade a score, not
# delete a card.


@pytest.mark.parametrize(
    "raw,expected",
    [
        (85, 1.0),  # a model answering in percent
        (1.2, 1.0),
        (-0.5, 0.0),
        (float("inf"), 1.0),
        (float("-inf"), 0.0),
        (float("nan"), 0.0),
        ("high", 0.0),  # not a number at all
        ("0.7", 0.7),  # numeric string is still a number
        (None, 0.0),
        ({}, 0.0),
    ],
)
def test_importance_is_clamped_and_never_raises(raw, expected):
    assert frame_for_event(_event(), importance=raw).importance == expected


# --- _plain neutralizes everything the validator refuses ------------------

ADVERSARIAL_SUBJECTS = [
    "**URGENT** [Verify](https://phish.example)",
    "*emphasis*",
    "_emphasis_",
    "~~strike~~",
    "`code`",
    "# heading",
    "Visit https://phish.example/now",
    "Visit www.phish.example",
    "Contact billing@phish.example today",
    "<mailto:x@y.z>",
    "<script>alert(1)</script>",
    "Wire funds ‮now",  # bidi override: RLO can reverse a headline
    "Wire\x07 funds",  # C0 control character
    "Wire\nfunds",  # embedded newline
    "a[.]b@c[.]d",  # a link disguised behind markdown punctuation
    "https://",  # a truncated scheme with nothing after it
    "**",  # entirely markdown: nothing survives
    "‮",  # entirely bidi: nothing survives
]


@pytest.mark.parametrize("subject", ADVERSARIAL_SUBJECTS)
def test_adversarial_subject_always_yields_a_frame(subject):
    """The invariant: whatever the validator refuses, _plain has removed.

    The two live in different modules and their alignment is otherwise
    accidental. Asserting the contracts-module pattern directly - rather than
    restating what _plain strips - makes this fail loudly if either side is
    changed alone. A raise here means the founder sees no card at all.
    """
    frame = frame_for_event(_event(title=subject, actor_entities=None))
    assert frame.headline
    assert _MARKDOWN_IN_HEADLINE.search(frame.headline) is None


@pytest.mark.parametrize("name", ADVERSARIAL_SUBJECTS)
def test_adversarial_actor_name_always_yields_a_frame(name):
    """The actor reaches the same headline field, so it takes the same route."""
    frame = frame_for_event(_event(actor_entities=[{"name": name}]))
    assert frame.headline
    assert _MARKDOWN_IN_HEADLINE.search(frame.headline) is None


def test_a_pre_ingest_raw_event_still_names_its_counterparty():
    """A RawEvent's actor field is `actor`, a NormalizedEvent's is
    `actor_entities`. perception_runner builds Units from RawEvents, before
    ingest has renamed anything, so reading only `actor_entities` silently
    dropped the person from every headline in the poll.
    """
    from src.services.event_processor import RawEvent

    event = RawEvent(
        source="gmail",
        source_account_id="acct_1",
        event_type="email_received",
        entity_type="email_thread",
        entity_id="t_1",
        title="Series A term sheet",
        actor={"type": "person", "name": "Sarah Chen"},
    )

    assert frame_for_event(event).headline == "Sarah Chen - Series A term sheet"


# --- A long subject is bounded, never refused -------------------------------
#
# `_plain` neutralizes a hostile subject rather than refusing it, and
# `_importance` clamps an out-of-range score rather than raising, both because
# a refusal means the founder never sees the card. Length is the same rule.
# The headline is code-authored from external text, so unlike the body there
# is no model to send a repair request to: a raise there can only drop a card.

_LONG_WORDS = "Series A term sheet review and diligence checklist for the board "


def test_a_subject_over_the_limit_still_yields_a_frame():
    frame = frame_for_event(_event(title=_LONG_WORDS * 8, actor_entities=None))
    assert frame.headline
    assert len(frame.headline) <= MAX_HEADLINE_CHARS


def test_a_clamped_headline_ends_on_a_word_boundary():
    """Cutting mid-token invents a word the sender never wrote."""
    subject = _LONG_WORDS * 8
    headline = frame_for_event(_event(title=subject, actor_entities=None)).headline

    assert subject.startswith(headline)
    assert subject[len(headline)] == " "


def test_a_clamped_headline_carries_no_ellipsis():
    """Spec §2.3: no ellipsis, no 'read more'. CSS line-clamp already says cut."""
    headline = frame_for_event(_event(title=_LONG_WORDS * 8, actor_entities=None)).headline

    assert "…" not in headline
    assert "..." not in headline


def test_the_budget_applies_after_the_actor_is_composed_in():
    """The actor prefix is part of the composed string, so a subject that fits
    on its own can still overrun once a name is prepended."""
    subject = "A" * (MAX_HEADLINE_CHARS - 4)
    frame = frame_for_event(_event(title=subject, actor_entities={"name": "Sarah Chen"}))

    assert frame.headline.startswith("Sarah Chen - ")
    assert len(frame.headline) <= MAX_HEADLINE_CHARS


def test_an_unbroken_token_is_hard_cut():
    """The word-boundary search must not fail closed when there is no boundary."""
    frame = frame_for_event(_event(title="A" * 300, actor_entities=None))

    assert len(frame.headline) == MAX_HEADLINE_CHARS
    assert set(frame.headline) == {"A"}
