"""Step 10B Phase 3a: DivergenceComparator is the PURE (no I/O) diff engine at
the heart of the shadow-compare harness. It diffs a captured ``ShadowDecision``
from the authoritative runtime against one from the non-authoritative (shadow)
runtime on five axes: route, write_intent_set, final_text, gate_verdict, and
read_synthesis — WITHOUT ever comparing transport details (that's the B12
boundary between native-stream and legacy frames).
"""

from src.orchestrator.divergence import Divergence, DivergenceComparator, ShadowDecision


def _decision(**overrides) -> ShadowDecision:
    defaults = dict(
        route="presenter",
        final_text="Hello there",
        write_intents=frozenset(),
        gate_verdict=None,
        read_synthesis=None,
    )
    defaults.update(overrides)
    return ShadowDecision(**defaults)


def test_identical_decisions_yield_no_divergence():
    d = _decision()

    assert DivergenceComparator.compare(d, d) == []


def test_differing_write_intents_yield_one_divergence_naming_the_difference():
    auth = _decision(write_intents=frozenset({"email.send:gmail_send"}))
    shadow = _decision(write_intents=frozenset({"calendar.create:gcal_create"}))

    divergences = DivergenceComparator.compare(auth, shadow)

    assert len(divergences) == 1
    d = divergences[0]
    assert d.kind == "write_intent_set"
    assert "email.send:gmail_send" in d.detail
    assert "calendar.create:gcal_create" in d.detail


def test_identical_write_intents_yield_no_write_intent_divergence():
    shared = frozenset({"email.send:gmail_send"})
    auth = _decision(write_intents=shared)
    shadow = _decision(write_intents=shared)

    assert DivergenceComparator.compare(auth, shadow) == []


def test_differing_route_yields_route_divergence():
    auth = _decision(route="presenter")
    shadow = _decision(route="executor")

    divergences = DivergenceComparator.compare(auth, shadow)

    assert len(divergences) == 1
    assert divergences[0].kind == "route"
    assert "presenter" in divergences[0].detail
    assert "executor" in divergences[0].detail


def test_final_text_differing_only_by_whitespace_and_case_is_not_a_divergence():
    auth = _decision(final_text="Hello  World")
    shadow = _decision(final_text="hello world")

    assert DivergenceComparator.compare(auth, shadow) == []


def test_final_text_differing_only_by_leading_trailing_whitespace_is_not_a_divergence():
    auth = _decision(final_text="  Hello World  ")
    shadow = _decision(final_text="Hello World")

    assert DivergenceComparator.compare(auth, shadow) == []


def test_final_text_differing_in_wording_yields_final_text_divergence():
    auth = _decision(final_text="Your meeting is at 3pm")
    shadow = _decision(final_text="Your meeting is at 4pm")

    divergences = DivergenceComparator.compare(auth, shadow)

    assert len(divergences) == 1
    assert divergences[0].kind == "final_text"


def test_gate_verdict_both_present_and_differing_yields_divergence():
    auth = _decision(gate_verdict="approval_required")
    shadow = _decision(gate_verdict="auto_execute_silent")

    divergences = DivergenceComparator.compare(auth, shadow)

    assert len(divergences) == 1
    assert divergences[0].kind == "gate_verdict"
    assert "approval_required" in divergences[0].detail
    assert "auto_execute_silent" in divergences[0].detail


def test_gate_verdict_one_none_is_not_a_divergence():
    auth = _decision(gate_verdict="approval_required")
    shadow = _decision(gate_verdict=None)

    assert DivergenceComparator.compare(auth, shadow) == []


def test_gate_verdict_both_none_is_not_a_divergence():
    auth = _decision(gate_verdict=None)
    shadow = _decision(gate_verdict=None)

    assert DivergenceComparator.compare(auth, shadow) == []


def test_gate_verdict_both_present_and_equal_is_not_a_divergence():
    auth = _decision(gate_verdict="approval_required")
    shadow = _decision(gate_verdict="approval_required")

    assert DivergenceComparator.compare(auth, shadow) == []


def test_read_synthesis_both_present_and_differing_yields_divergence():
    auth = _decision(read_synthesis="Found 3 unread emails")
    shadow = _decision(read_synthesis="Found 5 unread emails")

    divergences = DivergenceComparator.compare(auth, shadow)

    assert len(divergences) == 1
    assert divergences[0].kind == "read_synthesis"


def test_read_synthesis_one_none_is_not_a_divergence():
    auth = _decision(read_synthesis="Found 3 unread emails")
    shadow = _decision(read_synthesis=None)

    assert DivergenceComparator.compare(auth, shadow) == []


def test_multiple_axes_diverging_returns_stable_order():
    auth = _decision(
        route="presenter",
        final_text="Your meeting is at 3pm",
        write_intents=frozenset({"email.send:gmail_send"}),
        gate_verdict="approval_required",
        read_synthesis="Found 3 unread emails",
    )
    shadow = _decision(
        route="executor",
        final_text="Your meeting is at 4pm",
        write_intents=frozenset({"calendar.create:gcal_create"}),
        gate_verdict="auto_execute_silent",
        read_synthesis="Found 5 unread emails",
    )

    divergences = DivergenceComparator.compare(auth, shadow)

    assert [d.kind for d in divergences] == [
        "route",
        "write_intent_set",
        "final_text",
        "gate_verdict",
        "read_synthesis",
    ]


def test_divergence_is_a_frozen_dataclass_shape():
    d = Divergence(kind="route", detail="x -> y")

    assert d.kind == "route"
    assert d.detail == "x -> y"
