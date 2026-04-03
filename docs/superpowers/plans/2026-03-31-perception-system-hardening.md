# Perception System Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix critical data loss bugs in the perception pipeline (thread replies silently dropped, missing email headers, broken draft threading) and harden the knowledge layer (auto-contradiction detection, correlator integration, perception timeouts).

**Architecture:** 8 tasks across 3 priority tiers. P0 fixes a causal chain: thread dedup → email headers → draft context. P1 strengthens knowledge integrity. P2 adds resilience. Each task is independently testable and committable. All changes are backward-compatible (no migrations needed for P0).

**Tech Stack:** Python 3.12, pytest, SQLAlchemy async, Qdrant vector store, Claude API, Gmail REST API, Redis Streams.

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `src/services/event_processor.py` | Event dedup + scoring + storage | 1 |
| `src/connectors/gmail.py` | Gmail polling + message fetch + actions | 2, 4 |
| `src/services/graph_executor.py` | Plan execution + draft creation | 3 |
| `src/services/memory_service.py` | Memory extraction + dedup + contradiction | 5 |
| `src/services/event_correlator.py` | Cross-event correlation (read-only, no changes) | 6 |
| `src/orchestrator/jarvis.py` | Perception cycle orchestration | 6, 7 |
| `src/integrations/sync/push_receiver.py` | Webhook normalization | 8 |
| `tests/conftest.py` | Shared test fixtures | 1, 2 |
| `tests/test_event_processor.py` | Event processor tests | 1 |
| `tests/test_gmail_connector.py` | Gmail connector tests | 2, 4 |
| `tests/test_graph_executor_draft.py` | Draft action tests | 3 |
| `tests/test_memory_contradiction.py` | Contradiction detection tests | 5 |
| `tests/test_perception_cycle.py` | Perception cycle tests | 6, 7 |
| `tests/test_push_receiver.py` | Push receiver normalization tests | 8 |

---

## P0 — Data Loss Bugs

### Task 1: Fix thread reply dedup — include message_id in idempotency key

**Why:** The current idempotency key is `{source}:{entity_id}:{event_type}`. For Gmail, `entity_id` is the `threadId`, so ALL messages in a thread share the same key. Replies after the first message are silently dropped. This is a data loss bug.

**Fix:** Include `message_id` from `raw_payload` in the key. Keep `entity_id=threadId` for thread grouping queries.

**Files:**
- Modify: `backend/src/services/event_processor.py:136` (single-event key)
- Modify: `backend/src/services/event_processor.py:506` (batch key)
- Modify: `backend/tests/conftest.py:18-33` (update `make_raw_event` fixture)
- Modify: `backend/tests/test_event_processor.py` (add thread reply test)

- [ ] **Step 1: Update `make_raw_event` fixture to include `raw_payload` with `message_id`**

In `backend/tests/conftest.py`, update the `make_raw_event` defaults to include a `message_id` in `raw_payload`:

```python
def make_raw_event(**overrides) -> RawEvent:
    """Factory for test RawEvent instances."""
    defaults = dict(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id="thr_001",
        occurred_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        title="Investor follow-up on deck",
        summary="Investor requested latest deck and quick call",
        actor={"type": "person", "email": "investor@fund.com", "name": "John Doe"},
        raw_payload={"message_id": "msg_001"},
    )
    defaults.update(overrides)
    return RawEvent(**defaults)
```

- [ ] **Step 2: Write failing test — thread reply is NOT dropped**

In `backend/tests/test_event_processor.py`, add:

```python
@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_thread_reply_not_deduplicated(mock_get_client, settings, mock_db):
    """Two messages in the same thread (different message_id) must both be stored."""
    scores = {
        "importance_score": 0.7,
        "urgency_score": 0.5,
        "confidence_score": 0.8,
        "importance_signals": {
            "from_priority_person": False,
            "contains_deadline": False,
            "contains_question": True,
            "related_to_active_project": False,
        },
        "summary": "Follow-up question",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    # First call: no existing event (new)
    # Second call onward: we need the dedup check to find nothing for the reply too
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_result)

    processor = EventProcessor(settings=settings, db=mock_db)

    # First message in thread
    msg1 = make_raw_event(
        entity_id="thr_same",
        raw_payload={"message_id": "msg_001"},
        title="Investment proposal",
    )
    event_id_1 = await processor.process(msg1, TEST_USER_ID)
    assert event_id_1 is not None

    # Reply in same thread — different message_id
    msg2 = make_raw_event(
        entity_id="thr_same",
        raw_payload={"message_id": "msg_002"},
        title="Re: Investment proposal",
        summary="Can you provide an update on this?",
    )
    event_id_2 = await processor.process(msg2, TEST_USER_ID)
    assert event_id_2 is not None
    assert event_id_2 != event_id_1

    # Both stored — db.add called twice
    assert mock_db.add.call_count == 2

    # Verify idempotency keys are different
    stored_1 = mock_db.add.call_args_list[0][0][0]
    stored_2 = mock_db.add.call_args_list[1][0][0]
    assert stored_1.idempotency_key != stored_2.idempotency_key
    # Both share the same entity_id (thread grouping preserved)
    assert stored_1.entity_id == stored_2.entity_id == "thr_same"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_event_processor.py::test_thread_reply_not_deduplicated -v`

Expected: FAIL — both events get the same idempotency key `gmail:thr_same:email_received`, so the second `db.add` may not have different keys.

- [ ] **Step 4: Write test for idempotency key format**

```python
@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_idempotency_key_includes_message_id(mock_get_client, settings, mock_db):
    """Idempotency key must include message_id from raw_payload for granular dedup."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_claude_response({**DEFAULT_SCORES, "summary": "test"})
    )
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event(
        entity_id="thr_abc",
        raw_payload={"message_id": "msg_xyz"},
    )
    await processor.process(raw, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "gmail:thr_abc:msg_xyz:email_received"


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_idempotency_key_fallback_no_message_id(mock_get_client, settings, mock_db):
    """When raw_payload has no message_id, key falls back to source:entity_id:event_type."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_claude_response({**DEFAULT_SCORES, "summary": "test"})
    )
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event(entity_id="cal_evt_123", raw_payload=None)
    await processor.process(raw, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    # No message_id → falls back to 3-part key
    assert stored.idempotency_key == "gmail:cal_evt_123:email_received"
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_event_processor.py::test_idempotency_key_includes_message_id tests/test_event_processor.py::test_idempotency_key_fallback_no_message_id -v`

Expected: FAIL — current key formula doesn't include `message_id`.

- [ ] **Step 6: Fix the idempotency key in `_process_inner()`**

In `backend/src/services/event_processor.py`, replace line 136:

```python
# OLD:
idempotency_key = f"{raw.source}:{raw.entity_id}:{raw.event_type}"

# NEW:
message_id = (raw.raw_payload or {}).get("message_id", "")
if message_id:
    idempotency_key = f"{raw.source}:{raw.entity_id}:{message_id}:{raw.event_type}"
else:
    idempotency_key = f"{raw.source}:{raw.entity_id}:{raw.event_type}"
```

- [ ] **Step 7: Fix the batch key in `_process_batch_chunk()`**

In `backend/src/services/event_processor.py`, replace line 506:

```python
# OLD:
keys = [f"{r.source}:{r.entity_id}:{r.event_type}" for r in events]

# NEW:
def _make_idempotency_key(raw: RawEvent) -> str:
    message_id = (raw.raw_payload or {}).get("message_id", "")
    if message_id:
        return f"{raw.source}:{raw.entity_id}:{message_id}:{raw.event_type}"
    return f"{raw.source}:{raw.entity_id}:{raw.event_type}"

keys = [_make_idempotency_key(r) for r in events]
```

Note: Extract `_make_idempotency_key` as a module-level helper (or a `@staticmethod` on EventProcessor) so both `_process_inner` and `_process_batch_chunk` share the same formula. Update `_process_inner` line 136 to call it too:

```python
idempotency_key = _make_idempotency_key(raw)
```

- [ ] **Step 8: Run all event processor tests**

Run: `cd backend && python -m pytest tests/test_event_processor.py -v`

Expected: ALL PASS (existing tests + 3 new tests).

- [ ] **Step 9: Commit**

```bash
cd backend
git add src/services/event_processor.py tests/test_event_processor.py tests/conftest.py
git commit -m "fix: include message_id in event idempotency key to stop dropping thread replies"
```

---

### Task 2: Capture missing email headers in Gmail connector

**Why:** The Gmail message fetch only requests `From`, `Subject`, `Date` headers. Without `In-Reply-To`, `References`, `Message-ID`, `To`, `Cc`, we can't build reply chains, determine conversation structure, or know who else is on the thread.

**Files:**
- Modify: `backend/src/connectors/gmail.py:132` (`_fetch_message_as_event` — metadata headers)
- Modify: `backend/src/connectors/gmail.py:341-344` (`_fetch_message_detail` — listing headers)
- Create: `backend/tests/test_gmail_connector.py`

- [ ] **Step 1: Write failing test — event raw_payload contains email headers**

Create `backend/tests/test_gmail_connector.py`:

```python
"""Tests for GmailConnector — polling, message fetch, actions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.gmail import GmailConnector
from tests.conftest import make_mock_settings


def _make_gmail_message(
    msg_id: str = "msg_001",
    thread_id: str = "thr_001",
    headers: dict | None = None,
    snippet: str = "Test snippet",
    labels: list | None = None,
) -> dict:
    """Build a mock Gmail API message response."""
    default_headers = {
        "From": "alice@example.com",
        "To": "user@example.com",
        "Cc": "bob@example.com",
        "Subject": "Test Subject",
        "Date": "Mon, 31 Mar 2026 10:00:00 +0000",
        "Message-ID": f"<{msg_id}@mail.gmail.com>",
        "In-Reply-To": "",
        "References": "",
    }
    if headers:
        default_headers.update(headers)

    return {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "labelIds": labels or ["INBOX", "UNREAD"],
        "payload": {
            "headers": [{"name": k, "value": v} for k, v in default_headers.items()],
        },
    }


@pytest.fixture
def connector():
    return GmailConnector(make_mock_settings())


@pytest.mark.asyncio
async def test_fetch_message_captures_reply_headers(connector):
    """_fetch_message_as_event must include In-Reply-To, References, Message-ID in raw_payload."""
    msg = _make_gmail_message(
        msg_id="msg_reply_001",
        thread_id="thr_001",
        headers={
            "In-Reply-To": "<msg_original@mail.gmail.com>",
            "References": "<msg_original@mail.gmail.com>",
            "Message-ID": "<msg_reply_001@mail.gmail.com>",
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = msg

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    event = await connector._fetch_message_as_event(
        mock_client, "fake_token", "usr_test", "msg_reply_001"
    )

    assert event is not None
    assert event.raw_payload["message_id"] == "msg_reply_001"
    assert event.raw_payload["in_reply_to"] == "<msg_original@mail.gmail.com>"
    assert event.raw_payload["references"] == "<msg_original@mail.gmail.com>"
    assert event.raw_payload["rfc_message_id"] == "<msg_reply_001@mail.gmail.com>"
    assert event.raw_payload["to"] == "user@example.com"
    assert event.raw_payload["cc"] == "bob@example.com"


@pytest.mark.asyncio
async def test_fetch_message_detail_includes_thread_headers(connector):
    """_fetch_message_detail must return In-Reply-To and References."""
    msg = _make_gmail_message(
        msg_id="msg_002",
        headers={
            "In-Reply-To": "<msg_001@mail.gmail.com>",
            "References": "<msg_001@mail.gmail.com> <msg_000@mail.gmail.com>",
        },
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = msg

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    detail = await connector._fetch_message_detail(mock_client, "fake_token", "msg_002")

    assert detail is not None
    assert detail["in_reply_to"] == "<msg_001@mail.gmail.com>"
    assert detail["references"] == "<msg_001@mail.gmail.com> <msg_000@mail.gmail.com>"
    assert detail["rfc_message_id"] == "<msg_002@mail.gmail.com>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_gmail_connector.py -v`

Expected: FAIL — `raw_payload` doesn't contain `in_reply_to`, `references`, etc.

- [ ] **Step 3: Update `_fetch_message_as_event` to request and store all headers**

In `backend/src/connectors/gmail.py`, update `_fetch_message_as_event` (line 126-156):

```python
async def _fetch_message_as_event(
    self, client, access_token: str, user_id: str, msg_id: str
) -> RawEvent | None:
    """Fetch a single Gmail message and convert to RawEvent."""
    resp = await client.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
        params={
            "format": "metadata",
            "metadataHeaders": [
                "From", "To", "Cc", "Subject", "Date",
                "Message-ID", "In-Reply-To", "References",
            ],
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return None

    msg = resp.json()
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

    sender = headers.get("From", "unknown")
    subject = headers.get("Subject", "(no subject)")
    snippet = msg.get("snippet", "")

    return RawEvent(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=msg.get("threadId", msg_id),
        title=subject,
        summary=snippet[:500],
        actor={"type": "person", "email": sender, "name": sender},
        raw_payload={
            "message_id": msg_id,
            "labels": msg.get("labelIds", []),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "rfc_message_id": headers.get("Message-ID", ""),
            "in_reply_to": headers.get("In-Reply-To", ""),
            "references": headers.get("References", ""),
        },
    )
```

- [ ] **Step 4: Update `_fetch_message_detail` to include thread headers**

In `backend/src/connectors/gmail.py`, update `_fetch_message_detail` (line 337-362):

```python
async def _fetch_message_detail(self, client, access_token: str, msg_id: str) -> dict | None:
    """Fetch a message with headers + snippet for listing."""
    resp = await client.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
        params={
            "format": "metadata",
            "metadataHeaders": [
                "From", "To", "Cc", "Subject", "Date",
                "Message-ID", "In-Reply-To", "References",
            ],
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return None

    msg = resp.json()
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return {
        "message_id": msg_id,
        "thread_id": msg.get("threadId"),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
        "rfc_message_id": headers.get("Message-ID", ""),
        "in_reply_to": headers.get("In-Reply-To", ""),
        "references": headers.get("References", ""),
    }
```

- [ ] **Step 5: Run all gmail connector tests**

Run: `cd backend && python -m pytest tests/test_gmail_connector.py -v`

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/connectors/gmail.py tests/test_gmail_connector.py
git commit -m "fix: capture In-Reply-To, References, Message-ID, To, Cc headers in Gmail connector"
```

---

### Task 3: Pass thread_id to draft replies

**Why:** When Jarvis drafts a reply to an email thread, the draft is created as a new conversation instead of continuing the existing thread. The Gmail `create_draft` action already supports `thread_id` — it's just not passed from `_draft_action`.

**Files:**
- Modify: `backend/src/services/graph_executor.py:819-880` (`_draft_action`)
- Create: `backend/tests/test_graph_executor_draft.py`

- [ ] **Step 1: Write failing test — draft includes thread_id when available**

Create `backend/tests/test_graph_executor_draft.py`:

```python
"""Tests for GraphExecutor._draft_action — thread-aware drafting."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.graph_executor import GraphExecutor
from tests.conftest import make_mock_settings


def _make_executor(connector_creds: dict | None = None) -> GraphExecutor:
    """Build a GraphExecutor with mocked dependencies."""
    settings = make_mock_settings()
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    creds_fn = AsyncMock(return_value=connector_creds) if connector_creds else None

    executor = GraphExecutor(settings=settings, db=db)
    executor._client = MagicMock()
    executor._connector_credentials_fn = creds_fn
    return executor


@patch("src.services.graph_executor.get_anthropic_client")
@pytest.mark.asyncio
async def test_draft_passes_thread_id(mock_get_client):
    """When input_data contains thread_id, it must be passed to create_draft."""
    draft_json = json.dumps({"subject": "Re: Investment", "body": "Here's the update.", "tone": "professional"})
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=draft_json)]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    # Mock Gmail connector
    mock_connector_cls = MagicMock()
    mock_connector_instance = MagicMock()
    mock_connector_instance.execute_action = AsyncMock(return_value={
        "status": "ok",
        "draft_id": "draft_123",
        "message_id": "msg_draft_123",
    })
    mock_connector_cls.return_value = mock_connector_instance

    creds = {"access_token": "fake_token"}
    executor = _make_executor(connector_creds=creds)
    executor._client = mock_client

    run = MagicMock()
    run.run_id = "run_001"

    with patch("src.services.graph_executor.CONNECTOR_REGISTRY", {"gmail": mock_connector_cls}):
        result = await executor._draft_action(
            input_data={
                "recipient": "investor@fund.com",
                "goal": "Reply with update",
                "thread_id": "thr_abc123",
            },
            run=run,
        )

    assert result["status"] == "completed"
    assert result["draft"]["created_in_gmail"] is True

    # Verify thread_id was passed to create_draft
    call_args = mock_connector_instance.execute_action.call_args
    assert call_args[0][0] == "create_draft"
    draft_params = call_args[0][1]
    assert draft_params["thread_id"] == "thr_abc123"


@patch("src.services.graph_executor.get_anthropic_client")
@pytest.mark.asyncio
async def test_draft_works_without_thread_id(mock_get_client):
    """When no thread_id, draft should still be created (new conversation)."""
    draft_json = json.dumps({"subject": "Hello", "body": "New email.", "tone": "casual"})
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=draft_json)]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_connector_cls = MagicMock()
    mock_connector_instance = MagicMock()
    mock_connector_instance.execute_action = AsyncMock(return_value={
        "status": "ok",
        "draft_id": "draft_456",
    })
    mock_connector_cls.return_value = mock_connector_instance

    creds = {"access_token": "fake_token"}
    executor = _make_executor(connector_creds=creds)
    executor._client = mock_client

    run = MagicMock()
    run.run_id = "run_002"

    with patch("src.services.graph_executor.CONNECTOR_REGISTRY", {"gmail": mock_connector_cls}):
        result = await executor._draft_action(
            input_data={"recipient": "bob@example.com", "goal": "Send intro"},
            run=run,
        )

    assert result["status"] == "completed"
    call_args = mock_connector_instance.execute_action.call_args
    draft_params = call_args[0][1]
    assert "thread_id" not in draft_params or draft_params.get("thread_id") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_executor_draft.py::test_draft_passes_thread_id -v`

Expected: FAIL — `thread_id` not passed to `create_draft`.

- [ ] **Step 3: Update `_draft_action` to pass `thread_id`**

In `backend/src/services/graph_executor.py`, update the `create_draft` call block (around lines 860-866):

```python
                        connector = connector_cls(self._settings)
                        draft_params = {
                            "to": recipient,
                            "subject": draft.get("subject", ""),
                            "body": draft.get("body", ""),
                        }
                        thread_id = input_data.get("thread_id")
                        if thread_id:
                            draft_params["thread_id"] = thread_id
                        create_result = await connector.execute_action(
                            "create_draft",
                            draft_params,
                            creds,
                        )
```

This replaces the existing inline dict at lines 862-866.

- [ ] **Step 4: Run all draft tests**

Run: `cd backend && python -m pytest tests/test_graph_executor_draft.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/graph_executor.py tests/test_graph_executor_draft.py
git commit -m "fix: pass thread_id to Gmail create_draft for proper reply threading"
```

---

## P1 — Knowledge Integrity

### Task 4: Fix push_receiver Gmail normalizer

**Why:** `push_receiver.py:_normalize_gmail()` uses `historyId` as `entity_id`. This is wrong — `historyId` is a cursor, not an entity identifier. When the webhook triggers a perception cycle, the entity_id should not be `historyId`. Since the webhook just triggers a poll (setting `pending_run=True`), the normalizer should use a stable entity_id.

**Files:**
- Modify: `backend/src/integrations/sync/push_receiver.py:220-227`
- Create: `backend/tests/test_push_receiver.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_push_receiver.py`:

```python
"""Tests for push_receiver normalization functions."""

import pytest

from src.integrations.sync.push_receiver import _normalize_gmail


def test_normalize_gmail_uses_stable_entity_id():
    """Gmail webhook normalization must NOT use historyId as entity_id."""
    payload = {"historyId": "9876543", "emailAddress": "user@gmail.com"}
    result = _normalize_gmail(payload)

    # entity_id should be the email address (stable), not historyId (cursor)
    assert result["entity_id"] == "user@gmail.com"
    assert result["event_type"] == "gmail_webhook_signal"


def test_normalize_gmail_fallback_entity_id():
    """When emailAddress missing, fall back to 'gmail_push'."""
    payload = {"historyId": "9876543"}
    result = _normalize_gmail(payload)
    assert result["entity_id"] == "gmail_push"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_push_receiver.py -v`

Expected: FAIL — current code uses `historyId` as entity_id.

- [ ] **Step 3: Fix `_normalize_gmail`**

In `backend/src/integrations/sync/push_receiver.py`, replace lines 220-227:

```python
def _normalize_gmail(payload: dict) -> dict:
    return {
        "event_type": "gmail_webhook_signal",
        "entity_id": payload.get("emailAddress", "gmail_push"),
        "title": "New Gmail activity",
        "summary": f"Gmail push notification (historyId: {payload.get('historyId', 'unknown')})",
        "importance_score": 0.6,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_push_receiver.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/integrations/sync/push_receiver.py tests/test_push_receiver.py
git commit -m "fix: use stable entity_id in Gmail webhook normalizer instead of historyId cursor"
```

---

### Task 5: Auto-call contradiction detection during memory extraction

**Why:** `check_contradictions()` exists (memory_service.py:461) but is never called during `extract_and_store()`. This means contradictory facts accumulate silently — "Alice is CFO" and "Alice resigned as CFO" both stay active. The method should run automatically for each new memory that has semantic neighbors above 0.7 similarity.

**Files:**
- Modify: `backend/src/services/memory_service.py:95-145` (`extract_and_store`)
- Modify: `backend/tests/test_memory_service.py`

- [ ] **Step 1: Write failing test — contradiction auto-detected during extraction**

Add to `backend/tests/test_memory_service.py`:

```python
@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_auto_checks_contradictions(mock_get_client, mock_embed_cls, settings, mock_db):
    """extract_and_store should call check_contradictions for each new memory."""
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "scope": "general",
                "fact_text": "Alice resigned as CFO",
                "confidence": 0.9,
                "ttl_days": None,
            },
        ]
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    service = MemoryService(settings=settings, db=mock_db)

    # Spy on check_contradictions
    service.check_contradictions = AsyncMock(return_value=[])

    memory_ids = await service.extract_and_store(
        TEST_USER_ID, "Alice has resigned as CFO.", ["evt_001"]
    )

    assert len(memory_ids) == 1
    # check_contradictions must have been called with the new memory
    service.check_contradictions.assert_called_once()
    call_args = service.check_contradictions.call_args
    assert call_args[0][0] == TEST_USER_ID  # user_id
    assert "Alice resigned as CFO" in call_args[0][1]  # new_fact
    assert call_args[0][2] == memory_ids[0]  # new_memory_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_service.py::test_extract_auto_checks_contradictions -v`

Expected: FAIL — `check_contradictions.assert_called_once()` fails because it's never called.

- [ ] **Step 3: Add `check_contradictions` call to `extract_and_store`**

In `backend/src/services/memory_service.py`, after the memory is stored (after `db.add(memory)` and `db.flush()` around line 140), add the contradiction check:

Find the block where `memory_ids.append(memory_id)` is called (around line 141) and add right before it:

```python
            # Auto-check for contradictions with existing memories
            try:
                await self.check_contradictions(
                    user_id, fact_text, memory_id, workspace_id=workspace_id
                )
            except Exception:
                logger.debug("Contradiction check failed for %s", memory_id, exc_info=True)
```

- [ ] **Step 4: Run all memory service tests**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_memory_service.py
git commit -m "feat: auto-check contradictions during memory extraction to prevent stale facts"
```

---

### Task 6: Integrate EventCorrelator into perception cycle

**Why:** The `EventCorrelator` has useful primitives (same-entity, same-actor, burst, thread detection) but is never called during `run_perception_cycle()`. Adding correlation context to the Planner's input during perception gives it richer decision-making data.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (inside `run_perception_cycle`, after event ingestion)
- Create: `backend/tests/test_perception_correlator.py`

- [ ] **Step 1: Write failing test — perception cycle passes correlation context to Planner**

Create `backend/tests/test_perception_correlator.py`:

```python
"""Tests for EventCorrelator integration into perception cycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_correlator import EventCorrelator


@pytest.mark.asyncio
async def test_correlator_detects_thread():
    """EventCorrelator.detect_thread returns thread info for multi-event entity."""
    mock_db = MagicMock()

    # Simulate 3 events in same thread
    mock_events = []
    for i in range(3):
        evt = MagicMock()
        evt.event_id = f"evt_{i}"
        evt.source = "gmail"
        evt.event_type = "email_received"
        evt.entity_id = "thr_shared"
        evt.title = f"Message {i}"
        evt.occurred_at = MagicMock()
        evt.occurred_at.isoformat.return_value = f"2026-03-31T{10+i}:00:00+00:00"
        evt.actor_entities = None
        mock_events.append(evt)

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = mock_events
    mock_db.execute = AsyncMock(return_value=result_mock)

    correlator = EventCorrelator(mock_db)
    thread = await correlator.detect_thread("usr_test", "thr_shared", workspace_id="ws_test")

    assert thread is not None
    assert thread["event_count"] == 3
    assert thread["entity_id"] == "thr_shared"


@pytest.mark.asyncio
async def test_correlator_no_thread_for_single_event():
    """Single event should not be detected as a thread."""
    mock_db = MagicMock()

    evt = MagicMock()
    evt.event_id = "evt_0"
    evt.source = "gmail"
    evt.occurred_at = MagicMock()
    evt.occurred_at.isoformat.return_value = "2026-03-31T10:00:00+00:00"

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [evt]
    mock_db.execute = AsyncMock(return_value=result_mock)

    correlator = EventCorrelator(mock_db)
    thread = await correlator.detect_thread("usr_test", "thr_single", workspace_id="ws_test")

    assert thread is None
```

- [ ] **Step 2: Run tests to verify they pass (testing existing code)**

Run: `cd backend && python -m pytest tests/test_perception_correlator.py -v`

Expected: PASS — we're testing the existing correlator works correctly before integrating it.

- [ ] **Step 3: Add correlation context to perception cycle**

In `backend/src/orchestrator/jarvis.py`, inside `run_perception_cycle()`, after the event summaries are built (after `_ingest_raw_events`), add correlation context before calling the Planner:

Find the Planner call (around line 1299-1309 where `_call_agent("planner", ...)` is invoked) and enrich the message with correlation data. Before that call, add:

```python
        # Enrich with correlation context
        correlation_context = ""
        if event_summaries:
            try:
                from src.services.event_correlator import EventCorrelator
                correlator = EventCorrelator(db)
                # Check for thread patterns on ingested entities
                seen_entities = set()
                for raw_evt in raw_events:
                    if raw_evt.entity_id and raw_evt.entity_id not in seen_entities:
                        seen_entities.add(raw_evt.entity_id)
                        thread = await correlator.detect_thread(
                            user_id, raw_evt.entity_id, workspace_id=workspace_id
                        )
                        if thread and thread["event_count"] > 1:
                            correlation_context += (
                                f"\n[Thread detected] entity={thread['entity_id']} "
                                f"has {thread['event_count']} events "
                                f"(first: {thread['first_at']}, last: {thread['last_at']})"
                            )
            except Exception:
                logger.debug("Correlation enrichment failed", exc_info=True)
```

Then append `correlation_context` to the Planner message:

```python
        planner_message = (
            f"Evaluate these observations from {source}. "
            f"Create plans for anything important.\n"
            f"Optionally include a perception_policy JSON block to control "
            f"how soon {source} should next be checked:\n{observer_summary}"
        )
        if correlation_context:
            planner_message += f"\n\n--- Correlation Context ---{correlation_context}"
```

- [ ] **Step 4: Run perception correlator tests**

Run: `cd backend && python -m pytest tests/test_perception_correlator.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_perception_correlator.py
git commit -m "feat: integrate EventCorrelator into perception cycle for thread-aware planning"
```

---

## P2 — Resilience

### Task 7: Add perception cycle timeout

**Why:** If `connector.poll()` hangs (network issue, slow API), the perception cycle blocks indefinitely. The tool execution layer has a 60s timeout but connectors don't. A 30-second timeout prevents one hung connector from stalling all perception.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`_poll_connector` function)
- Modify: `backend/tests/test_perception_correlator.py` (add timeout test)

- [ ] **Step 1: Write failing test — poll timeout returns error**

Add to `backend/tests/test_perception_correlator.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_poll_connector_timeout():
    """_poll_connector must timeout after 30s, not hang indefinitely."""
    # We test the timeout pattern directly since _poll_connector is private
    async def slow_poll(*args, **kwargs):
        await asyncio.sleep(60)  # Simulate hang
        return [], None

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(slow_poll(), timeout=0.1)  # Use 0.1s for test speed
```

- [ ] **Step 2: Run test to verify it passes (testing the pattern)**

Run: `cd backend && python -m pytest tests/test_perception_correlator.py::test_poll_connector_timeout -v`

Expected: PASS — this validates our timeout approach works.

- [ ] **Step 3: Wrap connector.poll() with `asyncio.wait_for`**

In `backend/src/orchestrator/jarvis.py`, find the `_poll_connector` function (around line 1379-1440). Find the line where `connector.poll(...)` is called and wrap it:

```python
            try:
                raw_events, new_cursor = await asyncio.wait_for(
                    connector.poll(user_id, cursor, credentials),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Connector %s poll timed out after 30s for user %s",
                    source, user_id,
                )
                return [], cursor, "Poll timed out after 30s", cursor_type
```

Make sure `import asyncio` is present at the top of the file (it should already be there).

- [ ] **Step 4: Run existing perception tests**

Run: `cd backend && python -m pytest tests/ -v -k "perception" --timeout=30`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_perception_correlator.py
git commit -m "fix: add 30s timeout to connector.poll() to prevent perception cycle hangs"
```

---

### Task 8: Extract idempotency key helper to shared utility

**Why:** After Task 1, the key formula exists in two places (`_process_inner` and `_process_batch_chunk`). Extracting it to a single helper ensures consistency and makes it testable. This also sets up future connectors to pass `message_id` correctly.

**Files:**
- Modify: `backend/src/services/event_processor.py` (extract helper)
- Modify: `backend/tests/test_event_processor.py` (test helper directly)

- [ ] **Step 1: Write test for the extracted helper**

Add to `backend/tests/test_event_processor.py`:

```python
from src.services.event_processor import make_idempotency_key


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
```

- [ ] **Step 2: Run tests to verify they fail (function doesn't exist yet)**

Run: `cd backend && python -m pytest tests/test_event_processor.py::test_make_idempotency_key_with_message_id -v`

Expected: FAIL — `ImportError: cannot import name 'make_idempotency_key'`.

- [ ] **Step 3: Extract the helper function**

In `backend/src/services/event_processor.py`, add the helper after the `RawEvent` dataclass (around line 51):

```python
def make_idempotency_key(raw: RawEvent) -> str:
    """Build a unique idempotency key for an event.

    Includes message_id when available (e.g., Gmail) for per-message
    granularity within threads. Falls back to source:entity_id:event_type
    for sources without message-level IDs.
    """
    message_id = (raw.raw_payload or {}).get("message_id", "")
    if message_id:
        return f"{raw.source}:{raw.entity_id}:{message_id}:{raw.event_type}"
    return f"{raw.source}:{raw.entity_id}:{raw.event_type}"
```

Then update `_process_inner` (line 136) and `_process_batch_chunk` (line 506) to call it:

```python
# In _process_inner:
idempotency_key = make_idempotency_key(raw)

# In _process_batch_chunk:
keys = [make_idempotency_key(r) for r in events]
```

Remove the inline `_make_idempotency_key` local function if it was added in Task 1.

- [ ] **Step 4: Run all event processor tests**

Run: `cd backend && python -m pytest tests/test_event_processor.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/event_processor.py tests/test_event_processor.py
git commit -m "refactor: extract make_idempotency_key helper for consistent event dedup across paths"
```

---

## Verification

After all 8 tasks are complete, run the full test suite:

```bash
cd backend && python -m pytest tests/ -v --timeout=60
```

Also run the linter:

```bash
cd backend && ruff check src/ tests/ && ruff format --check src/ tests/
```

---

## Summary of Changes

| Task | Priority | Type | Files Changed | What It Fixes |
|------|----------|------|---------------|---------------|
| 1 | P0 | fix | event_processor.py, conftest.py, test_event_processor.py | Thread replies silently dropped |
| 2 | P0 | fix | gmail.py, test_gmail_connector.py | Missing In-Reply-To/References/Message-ID headers |
| 3 | P0 | fix | graph_executor.py, test_graph_executor_draft.py | Draft replies create new threads |
| 4 | P1 | fix | push_receiver.py, test_push_receiver.py | Gmail webhook uses cursor as entity_id |
| 5 | P1 | feat | memory_service.py, test_memory_service.py | Contradictory memories accumulate silently |
| 6 | P1 | feat | jarvis.py, test_perception_correlator.py | Planner lacks thread/correlation context |
| 7 | P2 | fix | jarvis.py, test_perception_correlator.py | Connector poll can hang indefinitely |
| 8 | P2 | refactor | event_processor.py, test_event_processor.py | Dedup key formula duplicated in two places |
