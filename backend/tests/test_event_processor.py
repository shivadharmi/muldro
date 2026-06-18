"""Tests for EventProcessor — scoring, dedup, normalization."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import DEFAULT_SCORES, EventProcessor, make_idempotency_key
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    # Default: no duplicate found
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_claude_response(scores: dict) -> MagicMock:
    """Build a mock Anthropic response with JSON content."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(scores))]
    return response


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_stores_event(mock_get_client, settings, mock_db):
    """Processing a new event should score it and store it."""
    scores = {
        "importance_score": 0.85,
        "urgency_score": 0.7,
        "confidence_score": 0.9,
        "importance_signals": {
            "from_priority_person": True,
            "contains_deadline": False,
            "contains_question": True,
            "related_to_active_project": True,
        },
        "summary": "Investor wants to discuss the deck",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    assert event_id.startswith("evt_")
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == 0.85
    assert stored_event.urgency_score == 0.7
    assert stored_event.status == "processed"


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_records_processing_latency(mock_get_client, settings, mock_db):
    """A stored event records perception-throughput latency for the source."""
    scores = {
        "importance_score": 0.5,
        "urgency_score": 0.3,
        "confidence_score": 0.9,
        "summary": "x",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()

    with patch("src.services.metrics_service.MetricsService") as mock_metrics:
        event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    mock_metrics.record_event_processing.assert_called_once()
    call = mock_metrics.record_event_processing.call_args
    assert call.args[0] == raw.source
    assert call.args[1] >= 0  # duration_ms


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_duplicate_does_not_record_latency(mock_get_client, settings, mock_db):
    """A duplicate (no event stored) must not record processing latency."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = "evt_existing"
    mock_db.execute = AsyncMock(return_value=result_mock)
    mock_get_client.return_value = MagicMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()

    with patch("src.services.metrics_service.MetricsService") as mock_metrics:
        event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is None
    mock_metrics.record_event_processing.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_deduplicates(mock_get_client, settings, mock_db):
    """Duplicate events (same idempotency key) should return None."""
    # Simulate existing event found
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = "evt_existing"
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_get_client.return_value = MagicMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is None
    mock_db.add.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_handles_concurrent_unique_violation(mock_get_client, settings, mock_db):
    """Concurrent ingestion that loses the race on idempotency_key must be
    treated as a duplicate, not raised to the caller.

    Regression test for the ``normalized_events.idempotency_key`` unique
    violation that surfaced as ``event_ingest_failed`` / DLQ entries when
    two perception cycles raced for the same source. The pre-check
    ``SELECT`` is non-atomic; the INSERT must catch ``IntegrityError``,
    roll back the session, and return ``None`` so callers (and downstream
    triggers/embedding/event bus publish) treat it as a no-op.
    """
    from sqlalchemy.exc import IntegrityError

    scores = {
        "importance_score": 0.5,
        "urgency_score": 0.5,
        "confidence_score": 0.5,
        "importance_signals": {},
        "summary": "duplicate from race",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    # Pre-check SELECT returns no existing row (race: both cycles miss).
    # commit() then raises IntegrityError — the other cycle won the race.
    mock_db.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("uq idempotency_key"))
    )
    mock_db.rollback = AsyncMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is None, "race-loser must be treated as duplicate"
    mock_db.rollback.assert_awaited_once()
    mock_db.add.assert_called_once()  # attempted, not silently skipped


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_score_fallback_on_error(mock_get_client, settings, mock_db):
    """If Claude scoring fails, default scores should be used."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == DEFAULT_SCORES["importance_score"]
    assert stored_event.urgency_score == DEFAULT_SCORES["urgency_score"]


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_thread_reply_not_deduplicated(mock_get_client, settings, mock_db):
    """Two messages in same thread (same entity_id) but different message_id both store."""
    scores = {
        "importance_score": 0.8,
        "urgency_score": 0.6,
        "confidence_score": 0.9,
        "importance_signals": {
            "from_priority_person": False,
            "contains_deadline": False,
            "contains_question": False,
            "related_to_active_project": False,
        },
        "summary": "thread message",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)

    msg1 = make_raw_event(entity_id="thr_100", raw_payload={"message_id": "msg_aaa"})
    msg2 = make_raw_event(entity_id="thr_100", raw_payload={"message_id": "msg_bbb"})

    eid1 = await processor.process(msg1, TEST_USER_ID)
    eid2 = await processor.process(msg2, TEST_USER_ID)

    assert eid1 is not None
    assert eid2 is not None
    assert mock_db.add.call_count == 2

    stored1 = mock_db.add.call_args_list[0][0][0]
    stored2 = mock_db.add.call_args_list[1][0][0]
    assert stored1.idempotency_key != stored2.idempotency_key
    assert stored1.entity_id == stored2.entity_id == "thr_100"


@pytest.mark.asyncio
async def test_idempotency_key_includes_message_id(settings, mock_db):
    """When raw_payload contains message_id, the key must include it as 4-part format."""
    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event(
        source="gmail",
        entity_id="thr_001",
        event_type="email_received",
        raw_payload={"message_id": "msg_xyz"},
    )

    # Call _process_inner directly to inspect the key (bypass semaphore)
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "gmail:thr_001:msg_xyz:email_received"


@pytest.mark.asyncio
async def test_idempotency_key_fallback_no_message_id(settings, mock_db):
    """When raw_payload has no message_id, key falls back to 3-part format."""
    processor = EventProcessor(settings=settings, db=mock_db)

    # Test with raw_payload=None
    raw_none = make_raw_event(
        source="slack",
        entity_id="ch_001",
        event_type="message",
        raw_payload=None,
    )
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw_none, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "slack:ch_001:message"

    # Reset mock and test with raw_payload that has no message_id key
    mock_db.add.reset_mock()
    raw_no_mid = make_raw_event(
        source="github",
        entity_id="pr_001",
        event_type="pr_opened",
        raw_payload={"some_other_field": "value"},
    )
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw_no_mid, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "github:pr_001:pr_opened"


def test_make_idempotency_key_with_message_id():
    """Key includes message_id when present in raw_payload."""
    raw = make_raw_event(
        source="gmail",
        entity_id="thr_abc",
        event_type="email_received",
        raw_payload={"message_id": "msg_xyz"},
    )
    assert make_idempotency_key(raw) == "gmail:thr_abc:msg_xyz:email_received"


def test_make_idempotency_key_without_message_id():
    """Key falls back to 3-part format when no message_id."""
    raw = make_raw_event(
        source="calendar",
        entity_id="cal_evt_123",
        event_type="meeting_scheduled",
        raw_payload=None,
    )
    assert make_idempotency_key(raw) == "calendar:cal_evt_123:meeting_scheduled"


def test_make_idempotency_key_empty_message_id():
    """Empty string message_id should use fallback format."""
    raw = make_raw_event(
        source="slack",
        entity_id="ch_001",
        event_type="message_posted",
        raw_payload={"message_id": ""},
    )
    assert make_idempotency_key(raw) == "slack:ch_001:message_posted"


# ── FIX #4 — trigger evaluation must be workspace-scoped ────────────────────


def _make_normalized_event(event_id: str = "evt_x", workspace_id: str = "ws_a"):
    """Minimal NormalizedEvent-like stub for _evaluate_triggers."""
    event = MagicMock()
    event.event_id = event_id
    event.workspace_id = workspace_id
    event.title = "t"
    event.event_type = "message"
    event.urgency_score = 0.5
    return event


@pytest.mark.asyncio
async def test_evaluate_triggers_filters_by_workspace(settings):
    """_evaluate_triggers must include a workspace_id predicate in its query so a
    trigger owned by another workspace under the same user cannot fire."""
    db = MagicMock()
    db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    processor = EventProcessor(settings=settings, db=db)
    event = _make_normalized_event(workspace_id="ws_a")

    await processor._evaluate_triggers(event, TEST_USER_ID, workspace_id="ws_a")

    db.execute.assert_awaited_once()
    stmt = db.execute.await_args[0][0]
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    # Both the user_id AND the workspace_id columns must appear in the WHERE clause.
    assert "user_id" in sql
    assert "workspace_id" in sql
    assert "ws_a" in sql


@pytest.mark.asyncio
async def test_evaluate_triggers_cross_workspace_does_not_fire(settings):
    """A trigger belonging to workspace B must NOT fire for an event in workspace A
    under the same user. Because the query is workspace-scoped, the DB returns no
    rows for ws_a (the ws_b trigger is filtered out at the query level), so no
    action executes and no trigger.fired event is published."""
    db = MagicMock()
    db.flush = AsyncMock()

    # Simulate the workspace-scoped query: querying ws_a returns NO triggers,
    # because the only trigger belongs to ws_b. A non-scoped query would have
    # returned it and fired it cross-tenant.
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    processor = EventProcessor(settings=settings, db=db)
    processor._execute_trigger_action = AsyncMock()
    processor._event_bus = MagicMock()
    processor._event_bus.publish = AsyncMock()

    event = _make_normalized_event(workspace_id="ws_a")
    await processor._evaluate_triggers(event, TEST_USER_ID, workspace_id="ws_a")

    # Nothing fired across the tenant boundary.
    processor._execute_trigger_action.assert_not_called()
    processor._event_bus.publish.assert_not_called()

    # And the query was genuinely scoped to ws_a.
    stmt = db.execute.await_args[0][0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "workspace_id" in sql and "ws_a" in sql


@pytest.mark.asyncio
async def test_evaluate_triggers_same_workspace_fires(settings):
    """A matching trigger in the SAME workspace as the event DOES fire."""
    db = MagicMock()
    db.flush = AsyncMock()

    trigger = MagicMock()
    trigger.trigger_id = "trg_a"
    trigger.name = "ws_a trigger"
    trigger.cooldown_until = None
    trigger.fire_count = 0
    trigger.action_config = {}
    trigger.action_type = "notify"

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [trigger]
    db.execute = AsyncMock(return_value=result_mock)

    processor = EventProcessor(settings=settings, db=db)
    processor._trigger_matches = MagicMock(return_value=True)
    processor._execute_trigger_action = AsyncMock()
    processor._event_bus = None  # skip publish path

    event = _make_normalized_event(workspace_id="ws_a")
    await processor._evaluate_triggers(event, TEST_USER_ID, workspace_id="ws_a")

    processor._execute_trigger_action.assert_awaited_once()
    assert trigger.fire_count == 1


def test_normalized_event_uniqueness_is_workspace_scoped():
    """SVC-P3-3 follow-up: NormalizedEvent uniqueness is per
    (workspace_id, idempotency_key), NOT global on idempotency_key alone.

    make_idempotency_key has no workspace/user component, so two workspaces
    connecting the SAME external account mint identical keys. A global unique
    constraint would reject the second workspace's event as a cross-tenant
    duplicate; the composite constraint isolates them.
    """
    from sqlalchemy import UniqueConstraint

    from src.models.events import NormalizedEvent

    unique_col_sets = {
        tuple(c.name for c in con.columns)
        for con in NormalizedEvent.__table__.constraints
        if isinstance(con, UniqueConstraint)
    }
    # Composite (workspace_id, idempotency_key) present; no global single-column
    # unique on idempotency_key alone.
    assert ("workspace_id", "idempotency_key") in unique_col_sets
    assert ("idempotency_key",) not in unique_col_sets
    assert not NormalizedEvent.__table__.c.idempotency_key.unique


# ── SVC-P3-3 — dedup/idempotency queries must be workspace-scoped ───────────
#
# NormalizedEvent uniqueness is now composite (workspace_id, idempotency_key)
# rather than global, because make_idempotency_key carries no workspace
# component. An un-scoped lookup could read or dedup an event across a tenant
# boundary, so every dedup query must include workspace_id. The invariant below
# is intentionally structural (any query touching idempotency_key must also be
# workspace-scoped) so it covers every current and future dedup site uniformly.


def _safe_result_mock() -> MagicMock:
    """Result stub that satisfies scalar_one_or_none / scalars().all() / .all()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    result.all.return_value = []
    return result


def _idempotency_query_sqls(db_mock: MagicMock) -> list[str]:
    """Compiled SQL for every execute() call whose query references idempotency_key."""
    sqls = []
    for call in db_mock.execute.await_args_list:
        stmt = call[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        if "idempotency_key" in compiled:
            sqls.append(compiled)
    return sqls


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_inner_dedup_query_is_workspace_scoped(mock_get_client, settings):
    """The single-event dedup lookup must filter by workspace_id, not key alone."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=_safe_result_mock())

    scores = {"importance_score": 0.5, "urgency_score": 0.5, "confidence_score": 0.9}
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=db)
    await processor.process(make_raw_event(), TEST_USER_ID, workspace_id="ws_a")

    sqls = _idempotency_query_sqls(db)
    assert sqls, "expected at least one idempotency_key dedup query"
    for sql in sqls:
        assert "workspace_id" in sql, f"dedup query not workspace-scoped: {sql}"
        assert "ws_a" in sql, f"dedup query did not bind the workspace: {sql}"


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_batch_dedup_and_refetch_queries_are_workspace_scoped(mock_get_client, settings):
    """Both the batch dedup lookup and the post-process re-fetch must be
    workspace-scoped — they filter NormalizedEvent by idempotency_key."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=_safe_result_mock())

    scores = {"importance_score": 0.5, "urgency_score": 0.5, "confidence_score": 0.9}
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=db)
    await processor._process_batch_chunk([make_raw_event()], TEST_USER_ID, "ws_a")

    sqls = _idempotency_query_sqls(db)
    # One dedup query + one post-process re-fetch, both touching idempotency_key.
    assert len(sqls) >= 2, f"expected dedup + re-fetch queries, got {len(sqls)}"
    for sql in sqls:
        assert "workspace_id" in sql, f"batch query not workspace-scoped: {sql}"
        assert "ws_a" in sql, f"batch query did not bind the workspace: {sql}"
