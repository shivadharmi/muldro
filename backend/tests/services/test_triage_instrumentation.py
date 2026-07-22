"""Task 12: triage's LLM classification records a token span.

The perception triage/extraction path calls the model via ``complete_text``, which
bypasses the deep-runtime budget middleware — so its cost was invisible (token_usage
held only trigger='chat' rows). ``_classify_llm`` now records a span with
trigger='perception', agent_name='triage', attributed to the batch's workspace.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.llm.utility import LLMUsage
from src.services.triage import TriageService


def _run(coro):
    return asyncio.run(coro)


def _raw(sender="x@y.com", title="t", summary="s", headers=None):
    return SimpleNamespace(
        actor={"email": sender},
        title=title,
        summary=summary,
        raw_payload={"headers": headers or {}},
    )


_LLM_JSON = (
    '[{"category":"work_thread","importance_score":0.8,"urgency_score":0.6,"confidence_score":0.9}]'
)
_USAGE = LLMUsage(model="claude-haiku-4-5-20251001", input_tokens=1200, output_tokens=40)


def test_classify_llm_records_perception_span():
    svc = TriageService()
    events = [_raw(sender="cofounder@startup.com", title="Board deck review")]
    with (
        patch(
            "src.services.triage.complete_text_with_usage",
            new=AsyncMock(return_value=(_LLM_JSON, _USAGE)),
        ),
        patch("src.services.triage.record_token_span", new=AsyncMock()) as mock_span,
    ):
        results = _run(svc.triage_batch(events, user_id="u", workspace_id="ws_1"))

    assert results[0].category == "work_thread"
    mock_span.assert_awaited_once()
    kw = mock_span.await_args.kwargs
    assert kw["trigger"] == "perception"
    assert kw["agent_name"] == "triage"
    assert kw["workspace_id"] == "ws_1"
    assert kw["model"] == "claude-haiku-4-5-20251001"
    assert kw["input_tokens"] == 1200
    assert kw["output_tokens"] == 40


def test_rule_classified_batch_records_no_span():
    """An all-rules batch never calls the LLM, so it records no span."""
    svc = TriageService()
    events = [_raw(headers={"List-Unsubscribe": "<x>"}, title="Sale")]
    with (
        patch("src.services.triage.complete_text_with_usage", new=AsyncMock()) as mock_llm,
        patch("src.services.triage.record_token_span", new=AsyncMock()) as mock_span,
    ):
        _run(svc.triage_batch(events, user_id="u", workspace_id="ws_1"))

    mock_llm.assert_not_called()
    mock_span.assert_not_called()
