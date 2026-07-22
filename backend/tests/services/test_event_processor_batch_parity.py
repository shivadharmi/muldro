"""Parity tests: `_process_batch_chunk` must match `process()`'s side effects.

A prior audit found the batch path was missing Qdrant embedding, IntegrityError
tolerance on commit, and Prometheus metrics — all present in `process()`
(`_process_inner`). These tests pin the embedding contract (the critical
regression: without it, event semantic search silently stops being populated
for batch-triaged events).

TriageService.triage_batch is patched at the class level (the import inside
`_score_events_batch` is local, so patching the class attribute is what
actually takes effect regardless of import site) to make importance scores
deterministic and avoid any LLM/network calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import EventProcessor
from src.services.triage import TriageResult
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event

WORKSPACE_ID = "ws_a"


def _dedup_result_mock() -> MagicMock:
    """Dedup query result: no existing events (nothing to skip)."""
    result = MagicMock()
    result.all.return_value = []
    return result


def _refetch_result_mock(event) -> MagicMock:
    """Post-process refetch result: return the given stored event."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = event
    return result


def _make_stored_event(raw, importance_score: float, event_id: str = "evt_test"):
    """Build a NormalizedEvent-like stand-in matching what the batch path
    would have stored for `raw`, with a controlled importance_score."""
    from src.models.events import NormalizedEvent

    return NormalizedEvent(
        event_id=event_id,
        user_id=TEST_USER_ID,
        workspace_id=WORKSPACE_ID,
        source=raw.source,
        source_account_id=raw.source_account_id,
        event_type=raw.event_type,
        entity_type=raw.entity_type,
        entity_id=raw.entity_id,
        occurred_at=raw.occurred_at,
        title=raw.title,
        summary=raw.summary,
        actor_entities=[raw.actor] if raw.actor else None,
        importance_signals={},
        urgency_score=0.5,
        importance_score=importance_score,
        confidence_score=0.9,
        idempotency_key="gmail:thr_001:msg_001:email_received",
        status="processed",
    )


def _make_processor(db, embedding_service=None, vector_store=None) -> EventProcessor:
    return EventProcessor(
        settings=make_mock_settings(),
        db=db,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )


def _triage_result(importance: float) -> TriageResult:
    return TriageResult(
        category="direct_request",
        tier="full",
        actionable=True,
        importance_score=importance,
        urgency_score=0.6,
        confidence_score=0.9,
        origin="llm",
    )


@patch("src.services.triage.TriageService.triage_batch")
@pytest.mark.asyncio
async def test_high_importance_event_is_embedded_into_vector_store(mock_triage_batch):
    """A batch event with importance_score >= 0.3 must be embedded and
    upserted into Qdrant — mirrors process()'s embedding block."""
    raw = make_raw_event()
    stored_event = _make_stored_event(raw, importance_score=0.8)
    mock_triage_batch.return_value = [_triage_result(0.8)]

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=[_dedup_result_mock(), _refetch_result_mock(stored_event)])

    embedding_service = MagicMock()
    embedding_service.embed_text = AsyncMock(return_value=[0.1] * 768)
    vector_store = MagicMock()
    vector_store.upsert = AsyncMock()

    processor = _make_processor(db, embedding_service, vector_store)
    processor._evaluate_triggers = AsyncMock()
    processor._evaluate_initiative = AsyncMock()

    results = await processor._process_batch_chunk([raw], TEST_USER_ID, WORKSPACE_ID)

    assert results[0] is not None
    vector_store.upsert.assert_awaited_once()
    call = vector_store.upsert.await_args
    assert call.kwargs["collection"] == "events"
    assert call.kwargs["id"] == stored_event.event_id
    assert call.kwargs["payload"]["importance_score"] == 0.8


@patch("src.services.triage.TriageService.triage_batch")
@pytest.mark.asyncio
async def test_low_importance_batch_is_not_embedded(mock_triage_batch):
    """A batch containing only low-importance events (< 0.3) must NOT be
    embedded — mirrors process()'s importance_score >= 0.3 gate."""
    raw = make_raw_event()
    stored_event = _make_stored_event(raw, importance_score=0.1)
    mock_triage_batch.return_value = [_triage_result(0.1)]

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=[_dedup_result_mock(), _refetch_result_mock(stored_event)])

    embedding_service = MagicMock()
    embedding_service.embed_text = AsyncMock(return_value=[0.1] * 768)
    vector_store = MagicMock()
    vector_store.upsert = AsyncMock()

    processor = _make_processor(db, embedding_service, vector_store)
    processor._evaluate_triggers = AsyncMock()
    processor._evaluate_initiative = AsyncMock()

    results = await processor._process_batch_chunk([raw], TEST_USER_ID, WORKSPACE_ID)

    assert results[0] is not None
    vector_store.upsert.assert_not_awaited()


@patch("src.services.metrics_service.MetricsService.record_event_ingested")
@patch("src.services.triage.TriageService.triage_batch")
@pytest.mark.asyncio
async def test_batch_commit_tolerates_integrity_error_as_dedup(
    mock_triage_batch, mock_record_ingested
):
    """A concurrent-duplicate race on commit must be tolerated (rollback +
    treat as dedup), mirroring process()'s `except IntegrityError` handling,
    instead of failing the whole chunk."""
    from sqlalchemy.exc import IntegrityError

    raw = make_raw_event()
    mock_triage_batch.return_value = [_triage_result(0.8)]

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))
    db.rollback = AsyncMock()
    db.execute = AsyncMock(side_effect=[_dedup_result_mock()])

    processor = _make_processor(db)
    processor._evaluate_triggers = AsyncMock()
    processor._evaluate_initiative = AsyncMock()

    results = await processor._process_batch_chunk([raw], TEST_USER_ID, WORKSPACE_ID)

    db.rollback.assert_awaited_once()
    assert results == [None]


@patch("src.services.triage.TriageService.triage_batch")
@pytest.mark.asyncio
async def test_batch_records_ingestion_metrics(mock_triage_batch):
    """A successfully stored batch event must record ingestion + processing
    metrics, mirroring process()'s MetricsService calls."""
    raw = make_raw_event()
    stored_event = _make_stored_event(raw, importance_score=0.1)
    mock_triage_batch.return_value = [_triage_result(0.1)]

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=[_dedup_result_mock(), _refetch_result_mock(stored_event)])

    processor = _make_processor(db)
    processor._evaluate_triggers = AsyncMock()
    processor._evaluate_initiative = AsyncMock()

    with patch(
        "src.services.metrics_service.MetricsService.record_event_ingested"
    ) as mock_ingested:
        await processor._process_batch_chunk([raw], TEST_USER_ID, WORKSPACE_ID)

    mock_ingested.assert_called_once_with(raw.source, raw.event_type)
