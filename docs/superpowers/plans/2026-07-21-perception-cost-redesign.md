# Perception Cost Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate email entity/memory extraction behind a cheap tiered triage so Jarvis stops spending Sonnet/Opus tokens on marketing and transactional noise.

**Architecture:** A single `TriageService` classifies each event (deterministic header rules first, then one batched Haiku call for the remainder) into `skip`/`light`/`full` + `actionable`. Triage replaces the per-email Sonnet scoring; its output is persisted on the event and gates (a) extraction depth in the worker consumers, (b) the double-extraction path, and (c) whether the autonomous Opus Planner wakes. Shipped in 5 flag-gated phases, shadow-mode first.

**Tech Stack:** Python 3.12, async SQLAlchemy (asyncpg), pydantic-settings, `complete_text` LLM helper, Redis event bus, Qdrant, Neo4j, pytest (custom `asyncio.run` hook — NO pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-07-21-perception-cost-redesign-design.md`

**Test harness reminders:**
- Full gate: `cd backend && uv run pytest tests/ --ignore=tests/e2e`
- NO `asyncio_mode` — tests are plain `async def`, driven by a custom `pytest_pyfunc_call` hook.
- Mock LLM by patching `complete_text` where it's *imported* (e.g. `src.services.triage.complete_text`), not at the source module.
- `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`.
- Commit messages: conventional, NO `Co-Authored-By`.

---

## File Structure

**New:**
- `backend/src/services/triage.py` — `TriageService`, `TriageResult`, taxonomy constants, deterministic rules. One responsibility: classify events into tiers.
- `backend/tests/services/test_triage.py` — unit tests for rules + batched classification + tier/actionable derivation.
- `backend/tests/services/test_extraction_gating.py` — tests that the worker gates on tier.
- `backend/alembic/versions/<rev>_backfill_triage_cleanup.py` — one-time re-triage + cascade delete migration (Phase 5).

**Modified:**
- `backend/src/config/settings.py` — add `perception_triage_enabled`, `perception_triage_shadow` flags.
- `backend/src/services/event_processor.py` — triage replaces `_score_events_batch`; batch publishes `event_processed`.
- `backend/src/orchestrator/connector_poller.py` — feed `process_batch` instead of per-event `process`.
- `backend/src/services/worker.py` — gate `_handle_entity_extraction` / `_handle_memory_extraction` on tier.
- `backend/src/orchestrator/perception_runner.py` — drop routine Librarian call; gate Planner on `actionable`.
- `backend/src/tools/intelligence_server/memory.py` — `store_memory` stops re-running `wm.extract_from_text`.
- `backend/src/services/budget.py` (or the middleware/util that writes `TokenUsage`) — span + correct `trigger` label.

---

## Phase 1 — Triage foundation (shadow mode)

Builds `TriageService`, wires it into batch scoring, persists tier — but does **not** gate anything yet. Lets us validate classifications against real events before they have teeth.

### Task 1: Settings flags

**Files:**
- Modify: `backend/src/config/settings.py`
- Test: `backend/tests/config/test_settings_triage.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/config/test_settings_triage.py
from src.config.settings import Settings


def test_triage_flags_default_off():
    s = Settings(anthropic_api_key="test", database_url="postgresql+asyncpg://x/y")
    assert s.perception_triage_enabled is False
    assert s.perception_triage_shadow is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/config/test_settings_triage.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'perception_triage_enabled'`

- [ ] **Step 3: Add the fields**

In `backend/src/config/settings.py`, alongside the other perception settings, add:

```python
    perception_triage_enabled: bool = False
    perception_triage_shadow: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/config/test_settings_triage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/config/settings.py backend/tests/config/test_settings_triage.py
git commit -m "feat(triage): add perception_triage_enabled/shadow settings flags"
```

### Task 2: Taxonomy + deterministic rules

**Files:**
- Create: `backend/src/services/triage.py`
- Test: `backend/tests/services/test_triage.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_triage.py
from src.services.event_processor import RawEvent
from src.services.triage import (
    CATEGORY_TIER,
    classify_by_rules,
    derive_tier,
    is_actionable,
)


def _raw(headers=None, sender="a@b.com", title="hi"):
    return RawEvent(
        source="gmail", source_account_id="acc", event_type="email_received",
        entity_type="email", entity_id="e1", title=title, summary="s",
        actor={"email": sender}, raw_payload={"headers": headers or {}},
    )


def test_unsubscribe_header_is_marketing():
    assert classify_by_rules(_raw(headers={"List-Unsubscribe": "<mailto:x>"})) == "marketing"


def test_precedence_bulk_is_marketing():
    assert classify_by_rules(_raw(headers={"Precedence": "bulk"})) == "marketing"


def test_plain_personal_mail_has_no_rule():
    assert classify_by_rules(_raw()) is None


def test_derive_tier_maps_category():
    assert derive_tier("marketing") == "skip"
    assert derive_tier("financial") == "light"
    assert derive_tier("security_alert") == "full"
    assert derive_tier("totally_unknown") == "full"  # fail-safe: unknown → full (recall)


def test_is_actionable_requires_category_and_urgency():
    assert is_actionable("security_alert", urgency=0.9) is True
    assert is_actionable("security_alert", urgency=0.1) is False
    assert is_actionable("marketing", urgency=0.9) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_triage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.triage'`

- [ ] **Step 3: Write the module**

```python
# backend/src/services/triage.py
"""Tiered event triage — classify events cheaply so extraction cost is
proportional to value. Deterministic header rules run first; the ambiguous
remainder is classified by one batched Haiku call (TriageService, Task 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["skip", "light", "full"]

# category → tier. Unknown categories fall through to "full" (recall-preserving).
CATEGORY_TIER: dict[str, Tier] = {
    "marketing": "skip",
    "newsletter": "skip",
    "social_notification": "skip",
    "delivery_ping": "skip",
    "financial": "light",
    "transactional": "light",
    "personal": "full",
    "work_thread": "full",
    "security_alert": "full",
    "calendar_invite": "full",
    "direct_request": "full",
}

ACTIONABLE_CATEGORIES = {
    "security_alert",
    "calendar_invite",
    "direct_request",
    "work_thread",
}
ACTIONABLE_URGENCY_THRESHOLD = 0.4
DEFAULT_CATEGORY = "personal"  # fail-safe when the LLM omits/garbles a category


def derive_tier(category: str) -> Tier:
    """Map a category to its extraction tier. Unknown → full (never silently drop)."""
    return CATEGORY_TIER.get(category, "full")


def is_actionable(category: str, urgency: float) -> bool:
    """Whether an event should be allowed to wake the Opus Planner."""
    return category in ACTIONABLE_CATEGORIES and urgency >= ACTIONABLE_URGENCY_THRESHOLD


def classify_by_rules(raw) -> str | None:
    """High-precision deterministic classification. Returns a category or None.

    Only fires for high-confidence *skip* signals so it never wrongly suppresses
    a real message: bulk-mail headers that legitimate marketing/newsletters are
    legally required to carry. Everything else defers to the LLM (Task 3).
    """
    payload = getattr(raw, "raw_payload", None) or {}
    headers = {str(k).lower(): str(v) for k, v in (payload.get("headers") or {}).items()}
    if "list-unsubscribe" in headers or "list-id" in headers:
        return "marketing"
    if headers.get("precedence", "").lower() in {"bulk", "list", "junk"}:
        return "marketing"
    return None


@dataclass
class TriageResult:
    category: str
    tier: Tier
    actionable: bool
    importance_score: float
    urgency_score: float
    confidence_score: float
    origin: Literal["rules", "llm", "default"]

    def to_signals(self) -> dict:
        """Serialize the triage fields for persistence in importance_signals."""
        return {
            "category": self.category,
            "tier": self.tier,
            "actionable": self.actionable,
            "triage_origin": self.origin,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/test_triage.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/triage.py backend/tests/services/test_triage.py
git commit -m "feat(triage): taxonomy, tier mapping, and deterministic header rules"
```

### Task 3: TriageService — batched Haiku classification

**Files:**
- Modify: `backend/src/services/triage.py`
- Test: `backend/tests/services/test_triage.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/services/test_triage.py
from unittest.mock import patch

from src.services.triage import TriageService


def test_triage_batch_rules_skip_llm_when_all_rule_classified():
    svc = TriageService()
    events = [
        _raw(headers={"List-Unsubscribe": "<x>"}, title="Sale"),
        _raw(headers={"Precedence": "bulk"}, title="Promo"),
    ]
    with patch("src.services.triage.complete_text") as mock_llm:
        results = _run(svc.triage_batch(events, user_id="u"))
    mock_llm.assert_not_called()  # zero LLM calls for an all-marketing batch
    assert [r.tier for r in results] == ["skip", "skip"]
    assert all(r.origin == "rules" for r in results)


def test_triage_batch_llm_classifies_remainder():
    svc = TriageService()
    events = [
        _raw(headers={"List-Unsubscribe": "<x>"}, title="Sale"),   # rule → marketing
        _raw(sender="cofounder@startup.com", title="Board deck review"),  # → llm
    ]
    llm_json = '[{"category":"work_thread","importance_score":0.8,"urgency_score":0.6,"confidence_score":0.9}]'
    with patch("src.services.triage.complete_text", return_value=llm_json) as mock_llm:
        results = _run(svc.triage_batch(events, user_id="u"))
    mock_llm.assert_called_once()  # only the 1 ambiguous event went to the LLM
    assert results[0].tier == "skip" and results[0].origin == "rules"
    assert results[1].tier == "full" and results[1].category == "work_thread"
    assert results[1].actionable is True  # work_thread + urgency 0.6 >= 0.4
```

Add this helper at the top of the test file if not already present:

```python
import asyncio


def _run(coro):
    return asyncio.run(coro)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_triage.py -k "triage_batch" -v`
Expected: FAIL — `ImportError: cannot import name 'TriageService'`

- [ ] **Step 3: Implement `TriageService`**

Append to `backend/src/services/triage.py`:

```python
import logging

from src.llm.utility import complete_text
from src.llm_utils import coerce_to_object, parse_llm_json

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """\
You are Jarvis's event triage engine. For each event, assign a category and \
importance/urgency/confidence scores (floats 0.0-1.0).

Categories (choose exactly one per event):
- marketing: promotions, offers, sales, ads
- newsletter: digests, subscriptions, bulk editorial
- social_notification: likes, follows, platform notifications
- delivery_ping: order/shipping/delivery status with no durable fact
- financial: card charges, receipts, invoices, subscription renewals
- transactional: account/service notices carrying a durable fact
- personal: mail from a real individual to the user
- work_thread: work/project discussion, colleagues, collaborators
- security_alert: logins, passkeys, password/2FA, suspicious activity
- calendar_invite: meeting invites/updates needing awareness or response
- direct_request: an explicit ask/question requiring the user to act

Respond with a JSON array of objects in the SAME ORDER as the events, each:
{"category": "<one of the above>", "importance_score": float, "urgency_score": float, "confidence_score": float}
"""


class TriageService:
    """Classify events into extraction tiers. Rules first, one batched Haiku
    call for the remainder. Failures fall back to full-tier (recall-preserving)."""

    def _default(self, origin: str = "default") -> TriageResult:
        return TriageResult(
            category=DEFAULT_CATEGORY,
            tier=derive_tier(DEFAULT_CATEGORY),
            actionable=False,
            importance_score=0.5,
            urgency_score=0.3,
            confidence_score=0.3,
            origin=origin,
        )

    def _from_llm_obj(self, obj: dict) -> TriageResult:
        category = str(obj.get("category") or DEFAULT_CATEGORY)
        urgency = float(obj.get("urgency_score", 0.3) or 0.3)
        return TriageResult(
            category=category,
            tier=derive_tier(category),
            actionable=is_actionable(category, urgency),
            importance_score=float(obj.get("importance_score", 0.5) or 0.5),
            urgency_score=urgency,
            confidence_score=float(obj.get("confidence_score", 0.3) or 0.3),
            origin="llm",
        )

    async def triage_batch(self, events: list, user_id: str) -> list[TriageResult]:
        results: list[TriageResult | None] = [None] * len(events)
        remainder: list[tuple[int, object]] = []

        # 1. Deterministic pass
        for i, raw in enumerate(events):
            cat = classify_by_rules(raw)
            if cat is not None:
                results[i] = TriageResult(
                    category=cat,
                    tier=derive_tier(cat),
                    actionable=False,
                    importance_score=0.05,
                    urgency_score=0.05,
                    confidence_score=0.9,
                    origin="rules",
                )
            else:
                remainder.append((i, raw))

        # 2. One batched Haiku call for the remainder
        if remainder:
            llm_results = await self._classify_llm([r for _, r in remainder])
            for (idx, _), res in zip(remainder, llm_results):
                results[idx] = res

        return [r if r is not None else self._default() for r in results]

    async def _classify_llm(self, events: list) -> list[TriageResult]:
        parts = []
        for i, raw in enumerate(events, 1):
            sender = (getattr(raw, "actor", None) or {}).get("email", "unknown")
            parts.append(
                f"Event {i}:\n  From: {sender}\n  Title: {getattr(raw, 'title', '') or ''}"
                f"\n  Summary: {getattr(raw, 'summary', '') or ''}"
            )
        user_msg = "Classify these events:\n\n" + "\n\n".join(parts)
        try:
            text = await complete_text(
                system=TRIAGE_SYSTEM_PROMPT,
                user=user_msg,
                tier="haiku",
                max_tokens=128 * len(events),
            )
            parsed = coerce_to_object(parse_llm_json(text))
            if isinstance(parsed, dict):
                parsed = parsed.get("events") or parsed.get("results") or [parsed]
            if isinstance(parsed, list) and len(parsed) == len(events):
                return [self._from_llm_obj(o) for o in parsed]
            logger.warning("Triage LLM returned %s results for %d events",
                           len(parsed) if isinstance(parsed, list) else "non-list", len(events))
        except Exception:
            logger.warning("Triage LLM failed; defaulting remainder to full", exc_info=True)
        return [self._default(origin="default") for _ in events]
```

> Note: `coerce_to_object` is the shared list-shape guard from `src.llm_utils` (added for the extractors — see project memory). Confirm the import path with `grep -n "def coerce_to_object" backend/src/llm_utils.py`; adjust if it lives elsewhere.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/test_triage.py -v`
Expected: PASS (all triage tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/triage.py backend/tests/services/test_triage.py
git commit -m "feat(triage): TriageService with rules-first, batched-Haiku classification"
```

### Task 4: Batch scoring uses triage + publishes event_processed (shadow persist)

**Files:**
- Modify: `backend/src/services/event_processor.py` (`_score_events_batch` ~688-726, `_process_batch_chunk` ~604-686)
- Test: `backend/tests/services/test_event_processor_triage.py`

> **Read first:** the current `_process_batch_chunk` does NOT publish `event_processed` — only `_evaluate_triggers`/`_evaluate_initiative`. The worker consumers extract on `event_processed`. So switching the poller to batch (Task 5) requires the batch path to publish that event, or extraction stops entirely. This task adds both the triage wiring and the missing publish.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_event_processor_triage.py
import asyncio
from unittest.mock import AsyncMock, patch

from src.services.event_processor import EventProcessor, RawEvent
from tests.conftest import make_mock_settings, TEST_USER_ID, TEST_WORKSPACE_ID


def _raw(title):
    return RawEvent(
        source="gmail", source_account_id="acc", event_type="email_received",
        entity_type="email", entity_id=f"e_{title}", title=title, summary="s",
        actor={"email": "x@y.com"}, raw_payload={"headers": {"List-Unsubscribe": "<x>"}},
    )


def test_batch_scoring_writes_tier_to_importance_signals(db_session):
    proc = EventProcessor(settings=make_mock_settings(), db=db_session)
    events = [_raw("Sale A"), _raw("Sale B")]
    # all marketing → rules classify → no LLM call
    scores = asyncio.run(proc._score_events_batch(events, TEST_USER_ID))
    assert scores[0]["importance_signals"]["tier"] == "skip"
    assert scores[0]["importance_signals"]["category"] == "marketing"
    assert scores[0]["importance_score"] == 0.05
```

> If a real `db_session` fixture is unavailable, construct with `db=None` and test `_score_events_batch` in isolation — it does not touch the DB. Follow the real-DB pattern in project memory (self-contained `_db_reachable`, NullPool, seed User→Workspace) only if you assert on stored rows.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_event_processor_triage.py -v`
Expected: FAIL — `KeyError: 'tier'` (scores lack triage fields)

- [ ] **Step 3: Rewrite `_score_events_batch` to use triage**

Replace the body of `_score_events_batch` in `backend/src/services/event_processor.py` with:

```python
    async def _score_events_batch(self, events: list[RawEvent], user_id: str) -> list[dict]:
        """Triage + score events in one batched call. Returns a per-event dict
        carrying scores AND triage fields (category/tier/actionable) in
        importance_signals. Triage is rules-first; only the ambiguous remainder
        hits Haiku."""
        from src.services.triage import TriageService

        triage_results = await TriageService().triage_batch(events, user_id)
        out: list[dict] = []
        for raw, tr in zip(events, triage_results):
            out.append(
                {
                    "importance_score": tr.importance_score,
                    "urgency_score": tr.urgency_score,
                    "confidence_score": tr.confidence_score,
                    "importance_signals": tr.to_signals(),
                    "summary": raw.summary,
                }
            )
        return out
```

- [ ] **Step 4: Add `event_processed` publish to the batch path**

In `_process_batch_chunk`, in the post-process loop (the `for raw, key in zip(events, keys):` block near the end that calls `_evaluate_triggers`), after a stored `ev` is found and before/after the trigger calls, add the publish that `process()` does per event:

```python
                    if ev:
                        if self._event_bus is not None:
                            await self._event_bus.publish_event(
                                "event_processed",
                                user_id,
                                {"event_id": ev.event_id},
                                workspace_id=workspace_id,
                            )
                        await self._evaluate_triggers(ev, user_id, workspace_id=workspace_id)
                        await self._evaluate_initiative(ev, user_id, workspace_id=workspace_id)
```

> **Verify the exact publish signature** against how `process()` publishes `event_processed` (grep `publish_event("event_processed"` in `event_processor.py`) and match it byte-for-byte — arg order/kwargs must be identical, else the worker filter misses it.

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/services/test_event_processor_triage.py -v && cd backend && uv run pytest tests/services/test_event_processor.py -v`
Expected: PASS (new test + existing event_processor tests still green)

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/event_processor.py backend/tests/services/test_event_processor_triage.py
git commit -m "feat(triage): batch scoring uses TriageService and publishes event_processed"
```

### Task 5: Poller feeds `process_batch`

**Files:**
- Modify: `backend/src/orchestrator/connector_poller.py` (~284-315, the `_ingest` loop)
- Test: `backend/tests/orchestrator/test_connector_poller_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator/test_connector_poller_batch.py
from unittest.mock import AsyncMock, patch


def test_ingest_calls_process_batch_not_per_event():
    # Arrange a poller with 3 raw events; assert process_batch is called once
    # with all 3, and per-event process() is NOT called.
    from src.orchestrator.connector_poller import ConnectorPoller  # adjust to real class

    with patch("src.services.event_processor.EventProcessor") as MockEP:
        instance = MockEP.return_value
        instance.process_batch = AsyncMock(return_value=["evt_1", "evt_2", "evt_3"])
        instance.process = AsyncMock()
        # ... construct poller + 3 raw events per the existing test setup in
        # tests/orchestrator/ and invoke _ingest ...
        # assert instance.process_batch.await_count == 1
        # assert instance.process.await_count == 0
```

> **Read first:** open `backend/src/orchestrator/connector_poller.py:266-315` and the existing poller tests in `tests/orchestrator/` to match the real class/method names and construction. Fill the arrange/act/assert using that setup — the assertions above are the contract.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator/test_connector_poller_batch.py -v`
Expected: FAIL — currently `_ingest` loops `process()` per event.

- [ ] **Step 3: Replace the per-event loop with a batch call**

In `_ingest`, replace the `for raw in raw_events: await processor.process(...)` loop with:

```python
            event_ids = await processor.process_batch(raw_events, user_id, workspace_id)
            summaries = [
                f"{raw.event_type}: {raw.title or raw.entity_id}"
                for raw, eid in zip(raw_events, event_ids)
                if eid is not None
            ]
```

> Preserve the existing return shape (it returns summary strings). Match the variable names used for `user_id`/`workspace_id` in the surrounding scope.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/test_connector_poller_batch.py tests/orchestrator/ -v -k "poller"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/connector_poller.py backend/tests/orchestrator/test_connector_poller_batch.py
git commit -m "perf(perception): poller feeds process_batch (batched triage) instead of per-event"
```

### Task 6: Gmail connector captures header signals

**Files:**
- Modify: the Gmail connector that builds `RawEvent` (find with `grep -rln "RawEvent(" backend/src/ | grep -i gmail` or search `backend/src/integrations/`/`backend/src/orchestrator/` for the gmail poll → RawEvent construction)
- Test: `backend/tests/.../test_gmail_headers.py` (mirror the connector's location)

- [ ] **Step 1: Write the failing test**

```python
def test_gmail_rawevent_includes_bulk_headers():
    # Given a Gmail message dict with a List-Unsubscribe header,
    # the built RawEvent.raw_payload["headers"] must contain it lowercased-key-safe.
    raw = build_raw_event_from_gmail(SAMPLE_MESSAGE_WITH_UNSUB)  # real fn name
    assert "List-Unsubscribe" in (raw.raw_payload["headers"])
```

> **Read first:** locate the Gmail message→`RawEvent` construction and its existing tests; use the real function name and a fixture message shape from those tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest <path> -v`
Expected: FAIL — headers not captured.

- [ ] **Step 3: Capture headers into raw_payload**

Where the Gmail connector builds the message payload, extract the relevant headers from the Gmail API `payload.headers` list and store them:

```python
            wanted = {"list-unsubscribe", "list-id", "precedence"}
            headers = {
                h["name"]: h["value"]
                for h in (message.get("payload", {}).get("headers") or [])
                if h["name"].lower() in wanted
            }
            raw_payload = {**existing_payload, "headers": headers}
```

- [ ] **Step 4: Run test to verify it passes** — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add <connector> <test>
git commit -m "feat(triage): capture bulk-mail headers into gmail RawEvent for triage rules"
```

### Task 7: Shadow-mode validation checkpoint (manual, no code)

- [ ] **Step 1:** Set `JARVIS_PERCEPTION_TRIAGE_ENABLED=true` is NOT needed yet; triage now runs inside batch scoring unconditionally and persists tier. Restart the worker: `python run.py --worker`.
- [ ] **Step 2:** Trigger a poll (or wait for the scheduler), then query the tier distribution against real mail:

```bash
docker exec jarvis-postgres-1 psql -U jarvis -d jarvis -c "
SELECT importance_signals->>'category' cat, importance_signals->>'tier' tier, count(*)
FROM normalized_events WHERE source='gmail'
GROUP BY 1,2 ORDER BY 3 DESC;"
```

- [ ] **Step 3:** Manually eyeball: are any `skip` rows actually valuable? Any `full` rows obviously junk? If misclassified, tune `TRIAGE_SYSTEM_PROMPT` / rules in `triage.py` and re-run. **Do not proceed to Phase 2 until the distribution looks right.**

```bash
git commit --allow-empty -m "chore(triage): phase 1 shadow-mode validation checkpoint"
```

---

## Phase 2 — Gate extraction + collapse the double path

### Task 8: Gate the worker extraction handlers on tier

**Files:**
- Modify: `backend/src/services/worker.py` (`_handle_entity_extraction` ~203, `_handle_memory_extraction` ~266)
- Test: `backend/tests/services/test_extraction_gating.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_extraction_gating.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _tier_helper():
    from src.services.worker import _event_tier  # new helper (Step 3)
    return _event_tier


def test_event_tier_reads_signals():
    from src.services.worker import _event_tier
    ev = MagicMock()
    ev.importance_signals = {"tier": "skip"}
    assert _event_tier(ev) == "skip"
    ev.importance_signals = None
    assert _event_tier(ev) == "full"  # missing → full (recall)


def test_entity_extraction_skips_when_tier_skip(monkeypatch):
    # With triage enabled and a skip-tier event, world_model.extract_from_event
    # is never called.
    ...  # construct StreamConsumerManager with settings.perception_triage_enabled=True,
        # a skip-tier NormalizedEvent, patch WorldModel.extract_from_event as AsyncMock,
        # invoke _handle_entity_extraction, assert extract_from_event NOT awaited.
```

> Flesh out `test_entity_extraction_skips_when_tier_skip` using the existing worker-handler test setup in `tests/services/test_worker.py` (event-object shape, settings, patched vector store).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_extraction_gating.py -v`
Expected: FAIL — `ImportError: cannot import name '_event_tier'`

- [ ] **Step 3: Add the tier helper + gates**

In `backend/src/services/worker.py`, add a module-level helper:

```python
def _event_tier(ev) -> str:
    """Extraction tier persisted on the event. Missing/garbled → 'full' (recall)."""
    signals = getattr(ev, "importance_signals", None) or {}
    tier = signals.get("tier")
    return tier if tier in {"skip", "light", "full"} else "full"
```

In `_handle_entity_extraction`, after the `workspace_id` guard, fetch the event and gate. Replace the extraction block so it reads the event first:

```python
        from sqlalchemy import select
        from src.models.events import NormalizedEvent
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            ev = (await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )).scalar_one_or_none()
            if ev is None:
                return
            if self._settings.perception_triage_enabled and _event_tier(ev) == "skip":
                logger.info("Skip-tier event %s: no entity extraction", event_id)
                return

            world_model = WorldModel(
                settings=self._settings, db=db, vector_store=self._vector_store,
            )
            entity_ids = await world_model.extract_from_event(
                event_id, user_id, workspace_id=workspace_id
            )
            await db.commit()
            # ... (existing Neo4j sync block unchanged) ...
```

In `_handle_memory_extraction`, it already fetches `ev`. Right after `if not ev: return`, add:

```python
            if self._settings.perception_triage_enabled and _event_tier(ev) == "skip":
                logger.info("Skip-tier event %s: no memory extraction", event_id)
                return
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/services/test_extraction_gating.py tests/services/test_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/worker.py backend/tests/services/test_extraction_gating.py
git commit -m "feat(triage): gate worker entity/memory extraction on event tier"
```

### Task 9: Light-tier path (structured-fact capture)

**Files:**
- Modify: `backend/src/services/worker.py` (`_handle_memory_extraction` — light branch)
- Test: `backend/tests/services/test_extraction_gating.py`

> Light tier keeps a lightweight founder spend/receipt ledger without the full Sonnet fan-out. Minimal implementation: for `light`, run memory extraction (the durable-fact path) but SKIP relationship-heavy entity extraction. Entity extraction is skipped for light; memory extraction runs (it already produces `financial`/`fact` memories).

- [ ] **Step 1: Write the failing test**

```python
def test_light_tier_extracts_memory_but_not_entities(monkeypatch):
    # light-tier event: entity extraction skipped, memory extraction runs.
    ...  # patch WorldModel.extract_from_event + MemoryService.extract_and_store;
        # assert extract_from_event NOT awaited, extract_and_store awaited once.
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL

- [ ] **Step 3: Add the light branch**

In `_handle_entity_extraction`, extend the gate: entity extraction runs only for `full`:

```python
            tier = _event_tier(ev)
            if self._settings.perception_triage_enabled and tier in {"skip", "light"}:
                logger.info("Tier=%s event %s: no entity extraction", tier, event_id)
                return
```

Leave `_handle_memory_extraction` gating only `skip` (so `light` and `full` both extract memories). This yields: skip → nothing; light → memories only (the ledger); full → entities + memories.

- [ ] **Step 4: Run tests** — Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add backend/src/services/worker.py backend/tests/services/test_extraction_gating.py
git commit -m "feat(triage): light tier captures memories (ledger) but skips entity fan-out"
```

### Task 10: Collapse the double path — drop Librarian + stop tool re-extraction

**Files:**
- Modify: `backend/src/orchestrator/perception_runner.py` (~276-285, the Librarian `call_agent` block)
- Modify: `backend/src/tools/intelligence_server/memory.py` (~339, `store_memory` → `wm.extract_from_text`)
- Test: `backend/tests/orchestrator/test_perception_runner.py`, `backend/tests/tools/test_memory_tool.py`

- [ ] **Step 1: Write the failing test (perception runner)**

```python
def test_perception_cycle_skips_librarian_when_triage_enabled(monkeypatch):
    # With perception_triage_enabled, run_perception_cycle must NOT call
    # invoker.call_agent("librarian", ...). Worker consumers own extraction now.
    ...  # patch AgentInvoker.call_agent; run a cycle with events; assert
        # "librarian" not among call_agent invocations.
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (librarian still called)

- [ ] **Step 3: Gate the Librarian call**

Wrap the Step-2 Librarian block in `perception_runner.py`:

```python
            # Step 2: entity/memory extraction is owned by the worker consumers
            # (tier-gated). Skip the redundant agentic Librarian pass.
            librarian_result = None
            if not self._settings.perception_triage_enabled:
                librarian_result = await self._invoker.call_agent(
                    "librarian",
                    message=f"Process these observations from {source} and extract "
                    f"entities and memories:\n{observer_summary}",
                    user_id=user_id, trace=trace, workspace_id=workspace_id,
                )
```

> Check downstream uses of `librarian_result` in this function; guard any `.` access with `if librarian_result:`.

- [ ] **Step 4: Stop `store_memory` re-extraction**

In `intelligence_server/memory.py` near line 339, the `store_memory` implementation calls `wm.extract_from_text(...)`. Remove that call (memories are stored directly; entity extraction is the worker's job). Write a test first:

```python
def test_store_memory_does_not_reextract_entities(monkeypatch):
    # store_memory must not call WorldModel.extract_from_text.
    ...  # patch WorldModel.extract_from_text; call the store_memory tool fn;
        # assert extract_from_text NOT awaited.
```

Then delete the `extract_from_text` invocation (and any now-unused local vars), keeping the memory-store itself.

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/test_perception_runner.py tests/tools/test_memory_tool.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/perception_runner.py backend/src/tools/intelligence_server/memory.py backend/tests/
git commit -m "refactor(triage): collapse double extraction — worker owns it, drop Librarian + tool re-extract"
```

---

## Phase 3 — Planner fast-path

### Task 11: Skip the Opus Planner when nothing is actionable

**Files:**
- Modify: `backend/src/orchestrator/connector_poller.py` (`_ingest` return — expose actionable count) and `backend/src/orchestrator/perception_runner.py` (~435-455, the Planner block; ~143 synthesis)
- Test: `backend/tests/orchestrator/test_perception_runner.py`

> **Seam:** perception_runner must know whether this poll produced any `actionable` event. Simplest: after ingestion, query the just-stored events. Add a helper that counts actionable events for the batch.

- [ ] **Step 1: Write the failing test**

```python
def test_planner_skipped_when_no_actionable(monkeypatch):
    # All events skip/marketing → run_perception_cycle must NOT call
    # call_agent("planner", ...).
    ...  # events all skip-tier; patch call_agent; assert "planner" not called.


def test_planner_runs_when_actionable(monkeypatch):
    # One security_alert (actionable) → planner IS called once.
    ...
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (planner always called)

- [ ] **Step 3: Add an actionable check + gate**

Add a helper to `perception_runner.py`:

```python
    async def _has_actionable(self, raw_events: list, user_id: str, workspace_id: str) -> bool:
        """True if any just-ingested event was triaged actionable."""
        from sqlalchemy import select
        from src.models.events import NormalizedEvent

        keys = [make_idempotency_key(r) for r in raw_events]  # import from event_processor
        async with self._db_factory() as db:
            rows = (await db.execute(
                select(NormalizedEvent.importance_signals).where(
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.idempotency_key.in_(keys),
                )
            )).all()
        return any((r[0] or {}).get("actionable") for r in rows)
```

Gate the Step-3 Planner block:

```python
            planner_result = None
            run_planner = True
            if self._settings.perception_triage_enabled:
                run_planner = await self._has_actionable(raw_events, user_id, workspace_id)
            if run_planner:
                planner_result = await self._invoker.call_agent(
                    "planner", message=planner_message, user_id=user_id,
                    trace=trace, workspace_id=workspace_id,
                )
            else:
                logger.info("Perception poll for %s: no actionable events, skipping Planner", source)
```

Guard the downstream `_apply_perception_policy_from_planner` / `_queue_perception_plan` calls with `if planner_result:`.

- [ ] **Step 4: Gate cross-source synthesis** (~line 143)

Apply the same `perception_triage_enabled`-and-actionable guard to the cross-source synthesis Planner call so an all-noise multi-source tick doesn't wake Opus.

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/test_perception_runner.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/perception_runner.py backend/tests/orchestrator/test_perception_runner.py
git commit -m "perf(perception): skip Opus Planner + synthesis when no actionable events"
```

---

## Phase 4 — Instrumentation

### Task 12: Record token spans for triage/extraction + correct trigger label

**Files:**
- Modify: `backend/src/services/triage.py` (`_classify_llm` — record span), and the `TokenUsage` writer (`grep -rn "TokenUsage(" backend/src/` to find it; likely `budget.py` / a tracker)
- Test: `backend/tests/services/test_triage_instrumentation.py`

> **Read first:** find how `TokenUsage` rows are written today (`grep -rn "class TokenUsage\|TokenUsage(" backend/src/`). Reuse that writer. The direct `complete_text` extraction/triage calls currently bypass it; add a span with `trigger="perception"`.

- [ ] **Step 1: Write the failing test**

```python
def test_triage_llm_records_token_span(monkeypatch):
    # _classify_llm records a TokenUsage-style span with trigger="perception".
    recorded = []
    monkeypatch.setattr("src.services.triage.record_token_span", lambda **kw: recorded.append(kw))
    ...  # patch complete_text to return usage; call _classify_llm; assert
        # recorded[0]["trigger"] == "perception" and agent_name == "triage".
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (`record_token_span` not wired)

- [ ] **Step 3: Wire the span**

Depending on whether `complete_text` returns usage, either (a) have `complete_text` return token counts, or (b) record an estimate. Use the existing `TokenUsage` writer. Add a thin `record_token_span(agent_name, model, input_tokens, output_tokens, trigger, workspace_id)` call in `_classify_llm` after the LLM returns, with `trigger="perception"`, `agent_name="triage"`.

> If `complete_text` does not currently surface usage, the smaller-scope version of this task is: fix the existing budget middleware's hardcoded `trigger="chat"` to reflect the actual path (`perception` for autonomous/perception turns), and log a warning that direct-call extraction usage is estimated. Prefer surfacing real usage from `complete_text` if the helper supports it.

- [ ] **Step 4: Run tests** — Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add backend/src/services/triage.py backend/tests/services/test_triage_instrumentation.py
git commit -m "feat(triage): record perception-trigger token spans for triage/extraction"
```

---

## Phase 5 — One-time cleanup migration

### Task 13: Re-triage existing events + cascade-delete skip-only-sourced data

**Files:**
- Create: `backend/alembic/versions/<rev>_backfill_triage_cleanup.py`
- Test: `backend/tests/migrations/test_triage_cleanup.py`

> **Approach:** a data migration (or a standalone script invoked by the migration) that: (1) re-triages every `normalized_events` row via `classify_by_rules` + title/summary heuristics; (2) finds entities/memories/relationships whose `source_refs`/`source_event_ids` point ONLY to now-skip-tier events; (3) deletes them from Postgres AND cascades to Qdrant (vector store) + Neo4j. Dry-run first.

- [ ] **Step 1: Write the failing test (dry-run reports, deletes nothing)**

```python
def test_cleanup_dryrun_reports_skip_only_entities(db_session):
    # Seed: 1 skip-tier event + entity sourced only from it, 1 full-tier event
    # + entity sourced from it. Dry-run returns the skip-only entity id and
    # deletes nothing.
    from src.services.triage_cleanup import plan_cleanup  # new module
    report = asyncio.run(plan_cleanup(db_session, dry_run=True))
    assert skip_only_entity_id in report["entities_to_delete"]
    assert full_entity_id not in report["entities_to_delete"]
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (`triage_cleanup` missing)

- [ ] **Step 3: Implement `plan_cleanup` + `execute_cleanup`**

Create `backend/src/services/triage_cleanup.py` with:
- `async def plan_cleanup(db, dry_run=True) -> dict`: re-triage events (rules + `derive_tier`), collect skip-tier `event_id`s, then query entities/memories/relationships whose source refs are a subset of the skip set. Return counts + ids.
- `async def execute_cleanup(db, vector_store, graph_engine) -> dict`: run `plan_cleanup(dry_run=False)`, delete rows, cascade Qdrant deletes (by point id) and Neo4j node deletes (by entity_id).

> Use the existing cascade-delete helpers from `EvictionService` (`src/services/eviction_service.py`) — it already cascades entity/memory deletes to Qdrant + Neo4j. Reuse rather than reimplement.

- [ ] **Step 4: Write the alembic migration wrapper**

```python
# backend/alembic/versions/<rev>_backfill_triage_cleanup.py
"""backfill triage cleanup — remove skip-only-sourced derived data"""
# Down-migration is a no-op (deleted junk is re-derivable from events on re-poll).
# Runs execute_cleanup via a sync-wrapped async call. Guard behind an env check
# so it is a deliberate, one-time run — see EvictionService for the async-in-alembic pattern.
```

- [ ] **Step 5: Run tests + dry-run against live dev DB**

Run: `cd backend && uv run pytest tests/migrations/test_triage_cleanup.py -v`
Then dry-run against the live dev DB and eyeball the report BEFORE executing:

```bash
cd backend && uv run python -c "import asyncio; from src.services.triage_cleanup import plan_cleanup; ..."
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/triage_cleanup.py backend/alembic/versions/ backend/tests/migrations/test_triage_cleanup.py
git commit -m "feat(triage): one-time cleanup migration for skip-only-sourced junk entities/memories"
```

---

## Final verification

- [ ] Full gate green: `cd backend && uv run pytest tests/ --ignore=tests/e2e`
- [ ] `ruff check src/ tests/ && ruff format --check src/ tests/`
- [ ] Enable in dev: `JARVIS_PERCEPTION_TRIAGE_ENABLED=true`, restart worker, poll, confirm via `token_usage` that skip-tier events cost 0 extraction calls and no-actionable polls make 0 Opus calls.
- [ ] Confirm recall: full-tier (personal/work/security/calendar) mail still fully extracted.
- [ ] Update `CLAUDE.md` "Data Flow" / "Intelligence Loop" only if an architectural invariant changed (triage as the pre-extraction gate is worth one sentence).

---

## Spec coverage check

| Spec §  | Requirement | Task(s) |
|---|---|---|
| §2 | Score-is-triage, batched Haiku | 3, 4, 5 |
| §3 | Taxonomy + rules-first + tiers | 2, 3 |
| §3 | light = structured-fact ledger | 9 |
| §4 | Gmail header capture | 6 |
| §4 | Batch scoring / revive process_batch | 4, 5 |
| §4 | Worker owns extraction, gated | 8, 9 |
| §4 | Drop Librarian, stop tool re-extract | 10 |
| §4 | Planner + synthesis fast-path | 11 |
| §4 | Instrumentation + trigger label | 12 |
| §4 | Cleanup migration (PG+Qdrant+Neo4j) | 13 |
| §5 | Shadow mode first | 4, 7 |
| §6 | Flag rollback | 1, 8, 10, 11 |
| §6 | Dry-run cleanup | 13 |
