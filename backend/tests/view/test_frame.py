"""frame.key is deterministic and names a durable thing, not an occurrence.

Identity must be deterministic or dedup is not dedup: an inferred key lets two
runs of one pipeline mint two keys for one thing, which is why three polls of
one inbox produced three "New activity" cards.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.view.contracts import _MARKDOWN_IN_HEADLINE, MAX_HEADLINE_CHARS
from src.view.frame import ensure_aware_utc, frame_for_event


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
    """The event's score is LLM-authored.

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


# --- a name that is a bare address: the backstop ---------------------------
#
# `_plain` removes a bare email address because the headline validator refuses
# one, so an actor whose `name` IS an address resolved to "" - the person
# vanished from the headline and their quote was dropped as unattributed. The
# PRIMARY fix is in gmail.py, which splits its RFC 5322 `From` and stores the
# local part when there is no display name. These pin the downstream backstop,
# which covers rows written before that fix and any other source that hands a
# bare address in `name`.


def test_an_actor_name_that_is_a_bare_address_salvages_its_local_part():
    frame = frame_for_event(_event(actor_entities=[{"name": "sarah@acme.com"}]))
    assert frame.headline == "sarah - Series A term sheet"


def test_a_real_display_name_beats_another_entrys_bare_address():
    """Two passes, not one: a name is better attribution than a fragment."""
    frame = frame_for_event(
        _event(actor_entities=[{"name": "sarah@acme.com"}, {"name": "Sarah Chen"}])
    )
    assert frame.headline == "Sarah Chen - Series A term sheet"


def test_the_salvaged_local_part_is_still_reduced_to_inert_text():
    """The salvage re-enters `_plain`, so it cannot mint what the validator
    refuses. A local part that is itself an autolink salvages to nothing and
    the event stays unattributed, which is the correct way to fail."""
    frame = frame_for_event(_event(actor_entities=[{"name": "www.evil.example@x.example"}]))
    assert frame.headline == "Series A term sheet"


def test_an_email_field_is_still_not_a_name():
    """The backstop reads `name`/`canonical_name` only. `email` is an identity
    field, not a display field, and promoting it would change what an actor
    entry carrying ONLY an address means."""
    frame = frame_for_event(_event(actor_entities=[{"email": "sarah@acme.com"}]))
    assert frame.headline == "Series A term sheet"


# --- importance is clamped, never raised ----------------------------------
#
# Frame.importance is ge=0.0 le=1.0 and the caller supplies it - eventually
# that caller is the ranker. An out-of-range value must not raise here: a ValidationError
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


def _strip_verbose_comments(pattern: str) -> str:
    """Remove re.VERBOSE `#` comments, respecting escapes and character classes."""
    out: list[str] = []
    escaped = in_class = in_comment = False
    for ch in pattern:
        if in_comment:
            if ch == "\n":
                in_comment = False
                out.append(ch)
            continue
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if in_class:
            out.append(ch)
            if ch == "]":
                in_class = False
            continue
        if ch == "[":
            in_class = True
            out.append(ch)
            continue
        if ch == "#":
            in_comment = True
            continue
        out.append(ch)
    return "".join(out)


def _top_level_alternatives(pattern: str) -> list[str]:
    """Split a verbose regex into its top-level `|` alternatives.

    READ OFF the compiled pattern rather than restated as a second list. A
    hand-written copy of the alternatives would be a third thing to keep in
    sync, and the entire point of the test below is that no alternative can be
    added on one side alone.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    escaped = in_class = False
    for ch in _strip_verbose_comments(pattern):
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if in_class:
            current.append(ch)
            if ch == "]":
                in_class = False
            continue
        if ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return [" ".join(p.split()) for p in parts if p.split()]


HEADLINE_ALTERNATIVES = _top_level_alternatives(_MARKDOWN_IN_HEADLINE.pattern)


def test_the_alternatives_are_parsed_off_the_compiled_pattern():
    """A splitter that silently returned [] would make the witness test vacuous."""
    assert len(HEADLINE_ALTERNATIVES) > 1
    for alternative in HEADLINE_ALTERNATIVES:
        re.compile(alternative, re.VERBOSE | re.MULTILINE)  # each stands alone


@pytest.mark.parametrize("alternative", HEADLINE_ALTERNATIVES)
def test_every_refused_construct_has_a_witness_in_the_corpus(alternative):
    """The corpus must EXERCISE the validator, not merely coexist with it.

    `test_adversarial_subject_always_yields_a_frame` asserts the right thing
    over a fixed list, so an alternative added to _MARKDOWN_IN_HEADLINE with no
    matching corpus entry - `\\|` for GFM tables, `!\\[` for images - leaves the
    suite green while frame_for_event starts raising ValidationError on real
    subjects, which units_from_events swallows as a silently dropped card.
    Every refused construct therefore needs at least one subject that contains
    it; the pairing above then proves _plain removes it.
    """
    matcher = re.compile(alternative, re.VERBOSE | re.MULTILINE)
    assert any(matcher.search(subject) for subject in ADVERSARIAL_SUBJECTS), (
        f"_MARKDOWN_IN_HEADLINE refuses {alternative!r} but no ADVERSARIAL_SUBJECTS "
        "entry contains it, so nothing checks that _plain removes it"
    )


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
    """No ellipsis, no 'read more': CSS line-clamp already says cut."""
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


# --- a frame's two timestamps share ONE tz policy -------------------------
#
# `occurred_at` and `updated_at` are frequently derived from the same event by
# different call sites. When only one of them coerced naive -> UTC, a single
# Frame carried a naive `occurred_at` beside an aware `updated_at`: comparing
# the frame's own two fields raised TypeError, and `model_dump_json()` emitted
# "…T10:00:00" for one and "…T10:00:00Z" for the other. JavaScript reads the
# offsetless form as LOCAL time, so the card renders one instant hours apart
# (5.5h for an IST founder). notion_connector.py is the live source of a naive
# value; github_connector.py already closed the same hazard at its boundary.

_NAIVE = datetime(2026, 8, 21, 10, 0, 0)


def test_a_naive_occurred_at_reaches_the_frame_as_an_aware_datetime():
    frame = frame_for_event(_event(occurred_at=_NAIVE))
    assert frame.occurred_at.tzinfo is not None
    assert frame.occurred_at == _NAIVE.replace(tzinfo=timezone.utc)


def test_a_naive_updated_at_reaches_the_frame_as_an_aware_datetime():
    frame = frame_for_event(_event(), updated_at=_NAIVE)
    assert frame.updated_at.tzinfo is not None
    assert frame.updated_at == _NAIVE.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "occurred_at,updated_at",
    [
        (_NAIVE, None),
        (_NAIVE, _NAIVE),
        (_NAIVE, datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)),
        (datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc), _NAIVE),
        (None, None),
        ("not a datetime", None),
    ],
)
def test_a_frames_two_timestamps_are_always_both_aware(occurred_at, updated_at):
    """The invariant, stated as the operation that used to raise."""
    frame = frame_for_event(_event(occurred_at=occurred_at), updated_at=updated_at)

    assert frame.occurred_at.tzinfo is not None
    assert frame.updated_at.tzinfo is not None
    frame.updated_at - frame.occurred_at  # must not raise


def test_serialized_timestamps_both_carry_an_offset():
    """The silent half of the bug: an offsetless timestamp is parsed as LOCAL
    time by JavaScript, so the card renders the same instant hours apart."""
    payload = json.loads(frame_for_event(_event(occurred_at=_NAIVE)).model_dump_json())

    for field in ("occurred_at", "updated_at"):
        assert payload[field].endswith("Z") or payload[field][-6] in "+-", payload[field]


def test_an_offset_that_is_present_is_preserved_not_rewritten():
    """The instant is what matters; the source's own offset is information."""
    ist = timezone(timedelta(hours=5, minutes=30))
    stamped = datetime(2026, 8, 21, 15, 30, tzinfo=ist)

    assert frame_for_event(_event(occurred_at=stamped)).occurred_at == stamped


def test_a_missing_occurred_at_is_dated_now_and_is_aware():
    frame = frame_for_event(_event(occurred_at=None))
    assert frame.occurred_at.tzinfo is not None
    assert (datetime.now(timezone.utc) - frame.occurred_at).total_seconds() < 60


def test_ensure_aware_utc_treats_a_non_datetime_as_absent():
    """External payloads are the source of these values. A malformed one costs
    its own event, never the poll."""
    assert ensure_aware_utc("2026-08-21T10:00:00") is None
    assert ensure_aware_utc(None) is None
    assert ensure_aware_utc(1755777600) is None
