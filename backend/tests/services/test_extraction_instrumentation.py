"""Task-12 follow-up: world_model + memory extraction LLM calls record a token span.

Entity/memory extraction calls the model via ``complete_text``, which bypasses the
deep-runtime budget middleware — so its cost was invisible in ``token_usage`` (only
triage was instrumented). Each extraction now records a ``trigger='perception'`` span
attributed to the batch's workspace, mirroring ``TriageService._classify_llm``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.llm.utility import LLMUsage

_USAGE = LLMUsage(model="claude-sonnet-5", input_tokens=800, output_tokens=60)


def _run(coro):
    return asyncio.run(coro)


def test_world_model_extraction_records_perception_span():
    from src.services.world_model_extraction import WorldModelExtractionMixin

    mixin = WorldModelExtractionMixin.__new__(WorldModelExtractionMixin)
    event = SimpleNamespace(
        event_type="email",
        source="gmail",
        title="Board deck review",
        summary="Please review the Q3 board deck.",
        actor_entities=None,
    )
    with (
        patch(
            "src.services.world_model_extraction.complete_text_with_usage",
            new=AsyncMock(return_value=('{"entities": [], "relationships": []}', _USAGE)),
        ),
        patch(
            "src.services.world_model_extraction.record_token_span", new=AsyncMock()
        ) as mock_span,
    ):
        _run(mixin._call_extraction(event, workspace_id="ws_1"))

    mock_span.assert_awaited_once()
    kw = mock_span.await_args.kwargs
    assert kw["trigger"] == "perception"
    assert kw["agent_name"] == "world_model"
    assert kw["workspace_id"] == "ws_1"
    assert kw["model"] == "claude-sonnet-5"
    assert kw["input_tokens"] == 800
    assert kw["output_tokens"] == 60


def test_world_model_extraction_empty_workspace_still_calls_span_helper():
    """The span helper no-ops on empty workspace itself; the extraction still passes it through
    (attribution is the helper's concern, not the caller's)."""
    from src.services.world_model_extraction import WorldModelExtractionMixin

    mixin = WorldModelExtractionMixin.__new__(WorldModelExtractionMixin)
    event = SimpleNamespace(
        event_type="email", source="gmail", title="t", summary="s", actor_entities=None
    )
    with (
        patch(
            "src.services.world_model_extraction.complete_text_with_usage",
            new=AsyncMock(return_value=('{"entities": []}', _USAGE)),
        ),
        patch(
            "src.services.world_model_extraction.record_token_span", new=AsyncMock()
        ) as mock_span,
    ):
        _run(mixin._call_extraction(event))  # workspace_id defaults to ""

    kw = mock_span.await_args.kwargs
    assert kw["workspace_id"] == ""


def test_memory_extraction_records_perception_span():
    from src.services.memory_service.extraction import MemoryExtraction

    me = MemoryExtraction.__new__(MemoryExtraction)
    with (
        patch(
            "src.services.memory_service.extraction.complete_text_with_usage",
            new=AsyncMock(return_value=('{"memories": []}', _USAGE)),
        ),
        patch(
            "src.services.memory_service.extraction.record_token_span", new=AsyncMock()
        ) as mock_span,
    ):
        _run(me._call_extraction("Founder prefers morning meetings.", workspace_id="ws_1"))

    mock_span.assert_awaited_once()
    kw = mock_span.await_args.kwargs
    assert kw["trigger"] == "perception"
    assert kw["agent_name"] == "memory"
    assert kw["workspace_id"] == "ws_1"
    assert kw["input_tokens"] == 800
    assert kw["output_tokens"] == 60
