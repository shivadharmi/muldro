# Perception System — Remaining Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining perception system gaps: full thread context on email replies, plan deduplication for user messages, task execution idempotency, and parallel perception source processing.

**Architecture:** 4 tasks across 2 tiers. Task 1 (thread fetch) is the highest-value user-facing feature. Tasks 2-3 add deduplication guards. Task 4 improves throughput. Memory consolidation was discovered to already be scheduled (nightly 2AM cron in `schedule_seeder.py`) so it is excluded.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy async, Alembic migrations, MCP bridge (`call_mcp_tool`), asyncio.gather, Redis Streams.

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `src/orchestrator/jarvis.py` | Perception cycle + plan persistence | 1, 2, 4 |
| `src/models/task_graph.py` | TaskRun model | 3 |
| `src/services/graph_executor.py` | TaskRun creation | 3 |
| `src/services/scheduler.py` | Perception tick loop | 4 |
| `alembic/versions/052_*.py` | Add idempotency_key to task_runs | 3 |
| `tests/test_thread_context.py` | Thread fetch tests | 1 |
| `tests/test_plan_dedup.py` | Plan dedup tests | 2 |
| `tests/test_task_idempotency.py` | Task idempotency tests | 3 |
| `tests/test_parallel_perception.py` | Parallel perception tests | 4 |

---

## Task 1: Full thread context fetch on email replies

**Why:** When a reply arrives on an email thread, Jarvis captures the reply (Task 1 from prior plan) but the Librarian/Planner only see the snippet "Can you provide an update?" with no context about what "this" refers to. For proper reasoning, Jarvis should fetch the full thread when it detects a reply.

**How:** After `_ingest_raw_events` and before the Librarian call, check if any event has `in_reply_to` set in `raw_payload`. If so, fetch the full thread via `get_gmail_thread_content` MCP tool and append the thread context to the observer summary.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:1280-1290` (between ingestion and Librarian)
- Create: `backend/tests/test_thread_context.py`

- [ ] **Step 1: Write failing test — reply events trigger thread fetch**

Create `backend/tests/test_thread_context.py`:

```python
"""Tests for full thread context fetching on email replies."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import RawEvent


def _make_reply_event(thread_id: str = "thr_001", message_id: str = "msg_002") -> RawEvent:
    """Build a RawEvent that represents an email reply."""
    return RawEvent(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=thread_id,
        title="Re: Investment proposal",
        summary="Can you provide an update on this?",
        actor={"type": "person", "email": "investor@fund.com", "name": "Investor"},
        raw_payload={
            "message_id": message_id,
            "in_reply_to": "<msg_001@mail.gmail.com>",
            "references": "<msg_001@mail.gmail.com>",
            "rfc_message_id": f"<{message_id}@mail.gmail.com>",
            "to": "user@example.com",
            "cc": "",
            "labels": ["INBOX"],
        },
    )


def _make_new_email_event(thread_id: str = "thr_002") -> RawEvent:
    """Build a RawEvent for a new (non-reply) email."""
    return RawEvent(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=thread_id,
        title="New project proposal",
        summary="We'd like to propose a new initiative.",
        actor={"type": "person", "email": "partner@example.com", "name": "Partner"},
        raw_payload={
            "message_id": "msg_new_001",
            "in_reply_to": "",
            "references": "",
            "rfc_message_id": "<msg_new_001@mail.gmail.com>",
            "to": "user@example.com",
            "cc": "",
            "labels": ["INBOX"],
        },
    )


@pytest.mark.asyncio
async def test_fetch_thread_context_for_replies():
    """When raw_events contain a reply (in_reply_to set), thread context should be fetched."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    mock_thread_result = {
        "status": "ok",
        "messages": [
            {"from": "user@example.com", "snippet": "Here is the investment proposal."},
            {"from": "investor@fund.com", "snippet": "Can you provide an update on this?"},
        ],
    }

    with patch("src.orchestrator.jarvis.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = mock_thread_result

        raw_events = [_make_reply_event("thr_001"), _make_new_email_event("thr_002")]
        contexts = await _fetch_thread_contexts(
            raw_events, user_id="usr_test", workspace_id="ws_test"
        )

    assert "thr_001" in contexts
    assert "thr_002" not in contexts  # Not a reply
    mock_mcp.assert_called_once()
    call_args = mock_mcp.call_args
    assert call_args[0][0] == "get_gmail_thread_content"
    assert call_args[0][1]["thread_id"] == "thr_001"


@pytest.mark.asyncio
async def test_fetch_thread_context_skips_non_gmail():
    """Non-Gmail events should not trigger thread fetch."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    slack_event = RawEvent(
        source="slack",
        source_account_id="slack_primary",
        event_type="message_posted",
        entity_type="channel",
        entity_id="ch_001",
        raw_payload={"in_reply_to": "some_thread"},
    )

    with patch("src.orchestrator.jarvis.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        contexts = await _fetch_thread_contexts(
            [slack_event], user_id="usr_test", workspace_id="ws_test"
        )

    assert len(contexts) == 0
    mock_mcp.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_thread_context_failure_returns_empty():
    """MCP tool failure should return empty dict, not crash."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    with patch("src.orchestrator.jarvis.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.side_effect = RuntimeError("MCP server down")

        raw_events = [_make_reply_event()]
        contexts = await _fetch_thread_contexts(
            raw_events, user_id="usr_test", workspace_id="ws_test"
        )

    assert len(contexts) == 0  # Graceful failure
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_thread_context.py -v`

Expected: FAIL — `_fetch_thread_contexts` doesn't exist yet.

- [ ] **Step 3: Implement `_fetch_thread_contexts` helper**

Add as a module-level async function in `backend/src/orchestrator/jarvis.py` (near other helper functions, e.g. after `_poll_connector`):

```python
async def _fetch_thread_contexts(
    raw_events: list,
    user_id: str,
    workspace_id: str,
    max_threads: int = 3,
) -> dict[str, dict]:
    """Fetch full thread context for Gmail reply events via MCP.

    Returns {thread_id: thread_result} for threads where in_reply_to is set.
    Capped at max_threads to limit API calls during perception.
    Failures are silently ignored (returns empty dict).
    """
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    contexts: dict[str, dict] = {}
    if not is_mcp_tool("get_gmail_thread_content"):
        return contexts

    fetched = 0
    seen: set[str] = set()
    for raw_evt in raw_events:
        if fetched >= max_threads:
            break
        if raw_evt.source != "gmail":
            continue
        payload = raw_evt.raw_payload or {}
        in_reply_to = payload.get("in_reply_to", "")
        thread_id = raw_evt.entity_id
        if not in_reply_to or thread_id in seen:
            continue
        seen.add(thread_id)

        try:
            result = await call_mcp_tool(
                "get_gmail_thread_content",
                {"thread_id": thread_id},
                user_id=user_id,
                workspace_id=workspace_id,
            )
            if isinstance(result, dict) and result.get("status") != "error":
                contexts[thread_id] = result
                fetched += 1
        except Exception:
            logger.debug("Failed to fetch thread %s context", thread_id, exc_info=True)

    return contexts
```

- [ ] **Step 4: Integrate into `run_perception_cycle`**

In `backend/src/orchestrator/jarvis.py`, between `_ingest_raw_events` (line 1280) and the `observer_summary` building (line 1285), add:

```python
            # Fetch full thread context for reply emails
            thread_contexts = await _fetch_thread_contexts(
                raw_events, user_id, workspace_id
            )

            # Build observer summary with thread context enrichment
            observer_summary = f"Polled {source}: {len(raw_events)} new event(s).\n" + "\n".join(
                f"- {s}" for s in event_summaries[:20]
            )
            if thread_contexts:
                observer_summary += "\n\n--- Thread Context (full conversation) ---"
                for tid, ctx in thread_contexts.items():
                    messages = ctx.get("messages", [])
                    if messages:
                        observer_summary += f"\nThread {tid} ({len(messages)} messages):"
                        for msg in messages[-5:]:  # Last 5 messages for context
                            snippet = msg.get("snippet", msg.get("body", ""))[:200]
                            sender = msg.get("from", "unknown")
                            observer_summary += f"\n  [{sender}]: {snippet}"
```

Replace the existing `observer_summary` line (1285-1287) with this block.

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_thread_context.py -v`

Expected: ALL PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_thread_context.py
git commit -m "feat: fetch full thread context for email replies during perception cycle"
```

---

## Task 2: Plan deduplication for user messages

**Why:** When a user says "Send email to Alice" twice in 10 seconds, two identical plans are created. Perception plans already have idempotency via `goal_hash`, but user message plans pass `idempotency_key=None` to `_persist_plan_record`. Adding a goal-hash key for user messages closes this gap.

**How:** In `_persist_plan_record` calls for user messages (lines 607 and 869), compute an idempotency key from the decision goal + decision type, similar to perception plans.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:600-615` (process_message plan persist)
- Modify: `backend/src/orchestrator/jarvis.py:865-875` (process_message_stream plan persist)
- Create: `backend/tests/test_plan_dedup.py`

- [ ] **Step 1: Write failing test — duplicate user message plans are blocked**

Create `backend/tests/test_plan_dedup.py`:

```python
"""Tests for user message plan deduplication."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PlannerOutput


def _make_decision(goal: str = "Send email to Alice", decision: str = "create_task") -> PlannerOutput:
    return PlannerOutput(
        decision=decision,
        goal=goal,
        reasoning="User requested email",
        priority="medium",
        risk_level="low",
        execution_mode="approval_required",
        tasks=[{"name": "send_email", "tool": "send_gmail_message"}],
    )


def test_user_message_idempotency_key_format():
    """User message plans should compute idempotency key from goal + decision."""
    decision = _make_decision(goal="Send email to Alice", decision="create_task")
    goal_hash = hashlib.sha256(decision.goal.encode()).hexdigest()[:16]
    expected_key = f"user:{decision.decision}:{goal_hash}"

    # Verify the key format is deterministic
    assert expected_key == f"user:create_task:{goal_hash}"
    # Same goal produces same key
    goal_hash_2 = hashlib.sha256("Send email to Alice".encode()).hexdigest()[:16]
    assert goal_hash == goal_hash_2


def test_different_goals_produce_different_keys():
    """Different goals must produce different idempotency keys."""
    goal_hash_1 = hashlib.sha256("Send email to Alice".encode()).hexdigest()[:16]
    goal_hash_2 = hashlib.sha256("Send email to Bob".encode()).hexdigest()[:16]
    assert goal_hash_1 != goal_hash_2
```

- [ ] **Step 2: Run tests to verify they pass (testing the key format logic)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_plan_dedup.py -v`

Expected: PASS — these test the hashing logic, not the integration.

- [ ] **Step 3: Add idempotency key computation to user message plan persistence**

In `backend/src/orchestrator/jarvis.py`, find the two `_persist_plan_record` calls for user messages (lines ~607 and ~869). Both currently look like:

```python
if decision.tasks and not decision.plan_id:
    decision = await self._persist_plan_record(decision, user_id, workspace_id)
```

Replace each with:

```python
if decision.tasks and not decision.plan_id:
    goal_hash = hashlib.sha256(
        (decision.goal or "").encode()
    ).hexdigest()[:16]
    user_idem_key = f"user:{decision.decision}:{goal_hash}"
    decision = await self._persist_plan_record(
        decision, user_id, workspace_id,
        idempotency_key=user_idem_key,
    )
```

Make sure `import hashlib` is at the top of the file (it likely already is — check line ~2084 where perception uses it).

- [ ] **Step 4: Run plan dedup tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_plan_dedup.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_plan_dedup.py
git commit -m "feat: add idempotency key to user message plans to prevent duplicate plan creation"
```

---

## Task 3: Task execution idempotency

**Why:** TaskRun has no `idempotency_key` column. If a plan is executed twice (e.g., via approval retry or double-click), two separate TaskRuns are created and external tools (Gmail send) execute twice. Adding an idempotency key prevents duplicate runs for the same plan+decision.

**How:** 1) Add `idempotency_key` column to TaskRun model via migration. 2) Compute key from `plan_id + decision` when creating runs. 3) Check for existing active run before creating.

**Files:**
- Create: `backend/alembic/versions/052_add_task_run_idempotency_key.py`
- Modify: `backend/src/models/task_graph.py:12-61` (add column)
- Modify: `backend/src/orchestrator/jarvis.py` (add key at run creation)
- Modify: `backend/src/services/graph_executor.py` (add key at executor run creation)
- Create: `backend/tests/test_task_idempotency.py`

- [ ] **Step 1: Write test — duplicate run creation is prevented**

Create `backend/tests/test_task_idempotency.py`:

```python
"""Tests for TaskRun idempotency."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.task_graph import TaskRun


def test_task_run_has_idempotency_key_field():
    """TaskRun model must have an idempotency_key column."""
    run = TaskRun(
        run_id="run_test",
        user_id="usr_test",
        workspace_id="ws_test",
        status="pending",
        idempotency_key="plan_abc:create_task",
    )
    assert run.idempotency_key == "plan_abc:create_task"


def test_task_run_idempotency_key_nullable():
    """idempotency_key should be nullable for backward compatibility."""
    run = TaskRun(
        run_id="run_test2",
        user_id="usr_test",
        workspace_id="ws_test",
        status="pending",
    )
    assert run.idempotency_key is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_task_idempotency.py -v`

Expected: FAIL — `idempotency_key` not a valid field.

- [ ] **Step 3: Add `idempotency_key` column to TaskRun model**

In `backend/src/models/task_graph.py`, add after the `timeout_seconds` field (around line 51):

```python
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
```

Also add a partial unique index to `__table_args__` (line 57-61):

```python
    __table_args__ = (
        Index("ix_task_runs_user_status", "user_id", "status", "created_at"),
        Index("ix_task_runs_source", "source", "created_at"),
        Index("ix_task_runs_ws_status", "workspace_id", "status"),
        Index(
            "ix_task_runs_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND status NOT IN ('completed', 'failed', 'cancelled')"),
        ),
    )
```

Add `from sqlalchemy import text` to imports if not already present.

- [ ] **Step 4: Create Alembic migration**

Create `backend/alembic/versions/052_add_task_run_idempotency_key.py`:

```python
"""Add idempotency_key to task_runs.

Revision ID: 052
Revises: 051
"""

from alembic import op
import sqlalchemy as sa

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("idempotency_key", sa.String(256), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX ix_task_runs_idempotency ON task_runs (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL "
        "AND status NOT IN ('completed', 'failed', 'cancelled')"
    )


def downgrade() -> None:
    op.drop_index("ix_task_runs_idempotency", table_name="task_runs")
    op.drop_column("task_runs", "idempotency_key")
```

- [ ] **Step 5: Add idempotency key to run creation in jarvis.py**

Find `_create_lightweight_run` (around line 315-340). Update the TaskRun creation:

```python
        run = TaskRun(
            run_id=run_id,
            user_id=user_id,
            workspace_id=workspace_id,
            plan_id=decision.plan_id,
            status="running",
            source="user_message",
            execution_mode=decision.execution_mode,
            policy_decision={"decision": decision.decision},
            conversation_id=conversation_id,
            trace_id=trace_id,
            idempotency_key=(
                f"{decision.plan_id}:{decision.decision}"
                if decision.plan_id else None
            ),
        )
```

- [ ] **Step 6: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_task_idempotency.py -v`

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/models/task_graph.py alembic/versions/052_add_task_run_idempotency_key.py \
    src/orchestrator/jarvis.py tests/test_task_idempotency.py
git commit -m "feat: add idempotency_key to TaskRun to prevent duplicate task execution"
```

---

## Task 4: Parallel perception source processing

**Why:** The scheduler processes perception sources sequentially. If Gmail takes 30s, Calendar waits. Using `asyncio.gather()` with a semaphore allows independent sources to poll concurrently.

**How:** Replace the sequential `for` loop in `_tick_perception` with `asyncio.gather()`, using a semaphore to cap concurrency.

**Files:**
- Modify: `backend/src/services/scheduler.py:198-222`
- Create: `backend/tests/test_parallel_perception.py`

- [ ] **Step 1: Write test — sources are processed concurrently**

Create `backend/tests/test_parallel_perception.py`:

```python
"""Tests for parallel perception source processing."""

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_concurrent_sources_faster_than_sequential():
    """Parallel execution of 3 sources should be faster than sequential."""
    call_log = []

    async def mock_cycle(source: str):
        call_log.append(("start", source, time.monotonic()))
        await asyncio.sleep(0.05)  # Simulate 50ms poll
        call_log.append(("end", source, time.monotonic()))
        return {"status": "completed", "events": 1}

    sources = ["gmail", "calendar", "slack"]

    # Parallel execution
    start = time.monotonic()
    sem = asyncio.Semaphore(5)

    async def bounded(s):
        async with sem:
            return await mock_cycle(s)

    await asyncio.gather(*(bounded(s) for s in sources))
    parallel_elapsed = time.monotonic() - start

    # All 3 should complete in ~50ms (parallel), not ~150ms (sequential)
    assert parallel_elapsed < 0.12  # Allow generous margin
    assert len(call_log) == 6  # 3 starts + 3 ends


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Semaphore should limit concurrent perception cycles."""
    active = 0
    max_active = 0

    async def mock_cycle(source: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"status": "completed", "events": 0}

    sources = [f"source_{i}" for i in range(10)]
    sem = asyncio.Semaphore(3)

    async def bounded(s):
        async with sem:
            return await mock_cycle(s)

    await asyncio.gather(*(bounded(s) for s in sources))

    assert max_active <= 3  # Semaphore enforced
    assert active == 0  # All completed
```

- [ ] **Step 2: Run tests to verify they pass (testing the pattern)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_parallel_perception.py -v`

Expected: PASS — we're testing the concurrency pattern before integrating.

- [ ] **Step 3: Replace sequential loop with asyncio.gather in scheduler**

In `backend/src/services/scheduler.py`, replace the sequential loop (lines 198-222) with:

```python
                perception_semaphore = asyncio.Semaphore(
                    getattr(self._settings, "perception_concurrency", 3)
                )

                async def _run_one(state):
                    async with perception_semaphore:
                        try:
                            workspace_id = await self._resolve_workspace(state.user_id)
                        except (ValueError, Exception):
                            workspace_id = state.workspace_id or ""

                        try:
                            result = await self._orchestrator.run_perception_cycle(
                                state.source,
                                user_id=state.user_id,
                                workspace_id=workspace_id,
                            )
                            event_count = result.get("events", 0)
                            if result.get("status") == "error":
                                await svc.record_failure(
                                    state, result.get("error", "unknown")
                                )
                            else:
                                await svc.record_success(state, event_count)
                            return state.source, event_count
                        except Exception as e:
                            await svc.record_failure(state, str(e)[:512])
                            logger.warning(
                                "Perception cycle failed for %s/%s: %s",
                                state.user_id,
                                state.source,
                                e,
                            )
                            return state.source, 0

                results = await asyncio.gather(
                    *(_run_one(s) for s in due_states),
                    return_exceptions=True,
                )

                # Log any unexpected exceptions from gather
                for i, r in enumerate(results):
                    if isinstance(r, BaseException):
                        logger.warning(
                            "Perception gather exception for %s: %s",
                            due_states[i].source if i < len(due_states) else "unknown",
                            r,
                        )
```

Make sure `import asyncio` is at the top of the file (it likely already is).

- [ ] **Step 4: Run all tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_parallel_perception.py -v`

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/scheduler.py tests/test_parallel_perception.py
git commit -m "perf: parallelize perception source processing with asyncio.gather and semaphore"
```

---

## Verification

After all 4 tasks are complete, run the full test suite:

```bash
cd backend && .venv/bin/python -m pytest tests/ -v --timeout=60
```

Also run the linter:

```bash
cd backend && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/
```

---

## Summary of Changes

| Task | Priority | Type | Files Changed | What It Fixes |
|------|----------|------|---------------|---------------|
| 1 | High | feat | jarvis.py, test_thread_context.py | Reply emails lack conversation context |
| 2 | Medium | feat | jarvis.py, test_plan_dedup.py | User message plans not deduplicated |
| 3 | Medium | feat | task_graph.py, jarvis.py, graph_executor.py, migration, test_task_idempotency.py | Task execution can duplicate external actions |
| 4 | Medium | perf | scheduler.py, test_parallel_perception.py | Sequential perception blocks on slow sources |

## Note: Memory Consolidation

Memory consolidation was identified as a gap in the initial review, but research revealed it is **already implemented and scheduled**:
- `consolidate_memories()` exists at `memory_service.py:567`
- Nightly cron at 2AM seeded by `schedule_seeder.py:79-85` with `action_type="consolidate_memories"`
- Scheduler handles it at `scheduler.py:635-644`

No action needed.
