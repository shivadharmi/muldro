"""One call, validated against the kind's budget, with its cost recorded.

No live model calls: `complete_text_with_usage` and `record_token_span` are
patched in this module's namespace rather than where they are defined, because
`body_generator` binds both names at import time (the same pattern as
tests/test_briefing.py's @patch("src.services.presenter.complete_text")).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.llm.utility import LLMUsage
from src.view.contracts import Frame, Quote

WHEN = datetime(2026, 8, 22, 14, 3, tzinfo=timezone.utc)
GOOD_BODY = "Sarah wants an answer on the term sheet by Friday.\n\nThree messages since Tuesday."


def _frame(kind="proposal"):
    return Frame(
        key="gmail:email_thread:t_1",
        kind=kind,
        status="needs_you",
        headline="Sarah Chen - Series A term sheet",
        source="gmail",
        entity_type="email_thread",
        occurred_at=WHEN,
        updated_at=WHEN,
        event_count=3,
    )


def _usage():
    return LLMUsage(model="claude-haiku", input_tokens=120, output_tokens=40)


def _completion(*texts):
    """An AsyncMock returning (text, usage) once per call, in order."""
    return AsyncMock(side_effect=[(text, _usage()) for text in texts])


async def test_a_valid_body_is_returned_unchanged():
    from src.view.body_generator import generate_body

    with (
        patch("src.view.body_generator.complete_text_with_usage", _completion(GOOD_BODY)),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        assert await generate_body(_frame(), [], workspace_id="ws_1") == GOOD_BODY


async def test_the_call_is_made_once_for_a_body_that_fits():
    from src.view.body_generator import generate_body

    call = _completion(GOOD_BODY)
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        await generate_body(_frame(), [], workspace_id="ws_1")
    assert call.await_count == 1


async def test_the_call_carries_the_workspace_so_its_model_binding_is_honoured():
    from src.view.body_generator import generate_body

    call = _completion(GOOD_BODY)
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        await generate_body(_frame(), [], workspace_id="ws_1")
    assert call.await_args.kwargs["workspace_id"] == "ws_1"


async def test_the_quotes_reach_the_prompt():
    from src.view.body_generator import generate_body

    call = _completion(GOOD_BODY)
    quote = Quote(text="Can you get back to me by Friday?", who="Sarah Chen", when=WHEN)
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        await generate_body(_frame(), [quote], workspace_id="ws_1")
    assert "Can you get back to me by Friday?" in call.await_args.kwargs["user"]


async def test_the_cost_is_recorded_as_a_perception_span():
    """A direct utility call bypasses the deep-runtime budget middleware and is
    otherwise invisible in cost accounting."""
    from src.view.body_generator import generate_body

    span = AsyncMock()
    with (
        patch("src.view.body_generator.complete_text_with_usage", _completion(GOOD_BODY)),
        patch("src.view.body_generator.record_token_span", span),
    ):
        await generate_body(_frame(), [], workspace_id="ws_1")
    assert span.await_count == 1
    assert span.await_args.kwargs["trigger"] == "perception"
    assert span.await_args.kwargs["workspace_id"] == "ws_1"
    assert span.await_args.kwargs["output_tokens"] == 40


async def test_surrounding_whitespace_is_stripped():
    from src.view.body_generator import generate_body

    padded = f"\n\n{GOOD_BODY}\n"
    with (
        patch("src.view.body_generator.complete_text_with_usage", _completion(padded)),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        assert await generate_body(_frame(), [], workspace_id="ws_1") == GOOD_BODY


# --- the repair loop -------------------------------------------------------

LONG_LEDE = "x" * 300  # a proposal allows 140


async def test_an_overrun_is_rewritten_not_truncated():
    from src.view.body_generator import generate_body

    with (
        patch(
            "src.view.body_generator.complete_text_with_usage",
            _completion(LONG_LEDE, GOOD_BODY),
        ),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        body = await generate_body(_frame(), [], workspace_id="ws_1")
    assert body == GOOD_BODY  # the second attempt, whole — not the first, cut


async def test_the_repair_prompt_carries_the_validators_own_message():
    from src.view.body_generator import generate_body

    call = _completion(LONG_LEDE, GOOD_BODY)
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        await generate_body(_frame(), [], workspace_id="ws_1")
    second = call.await_args_list[1].kwargs["user"]
    assert "300 characters" in second
    assert "allows 140" in second
    assert LONG_LEDE in second  # the model is shown what it wrote


async def test_the_loop_is_capped_at_three_attempts():
    from src.view.body_generator import generate_body

    call = _completion(LONG_LEDE, LONG_LEDE, LONG_LEDE)
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        await generate_body(_frame(), [], workspace_id="ws_1")
    assert call.await_count == 3


async def test_an_exhausted_cap_yields_no_prose_rather_than_no_card():
    """A card with no prose is strictly better than no card: the frame still
    carries the headline, the count, the timestamps and the quotes."""
    from src.view.body_generator import generate_body

    with (
        patch(
            "src.view.body_generator.complete_text_with_usage",
            _completion(LONG_LEDE, LONG_LEDE, LONG_LEDE),
        ),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        assert await generate_body(_frame(), [], workspace_id="ws_1") == ""


async def test_a_provider_error_is_transient_and_raises():
    from src.view.body_generator import BodyUnavailable, generate_body

    call = AsyncMock(side_effect=RuntimeError("429"))
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        with pytest.raises(BodyUnavailable):
            await generate_body(_frame(), [], workspace_id="ws_1")
    assert call.await_count == 1  # not retried here; the next poll retries


async def test_three_blank_completions_are_transient_and_raise():
    """Empty completions were a real provider-parameter bug, not a model
    refusing — so they must be retried later, never persisted as a give-up."""
    from src.view.body_generator import BodyUnavailable, generate_body

    with (
        patch("src.view.body_generator.complete_text_with_usage", _completion("", "  ", "")),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        with pytest.raises(BodyUnavailable):
            await generate_body(_frame(), [], workspace_id="ws_1")


async def test_a_blank_attempt_is_retried_and_a_later_good_one_wins():
    from src.view.body_generator import generate_body

    with (
        patch("src.view.body_generator.complete_text_with_usage", _completion("", GOOD_BODY)),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        assert await generate_body(_frame(), [], workspace_id="ws_1") == GOOD_BODY


async def test_an_unknown_kind_spends_nothing():
    """A kind added to the Literal without a LEDE_BUDGETS entry would make
    validate_body raise "unknown frame kind" — not a message a model can
    repair, so three calls would buy nothing. `model_construct` bypasses the
    Literal, which is how the test reaches the case at all."""
    from src.view.body_generator import generate_body

    call = _completion(GOOD_BODY)
    frame = Frame.model_construct(**{**_frame().model_dump(), "kind": "alert"})
    with (
        patch("src.view.body_generator.complete_text_with_usage", call),
        patch("src.view.body_generator.record_token_span", AsyncMock()),
    ):
        assert await generate_body(frame, [], workspace_id="ws_1") == ""
    assert call.await_count == 0
