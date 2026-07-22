# A2UI Surface Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make persisted/pushed A2UI surfaces carry structured, typed, non-duplicated data; propagate the verification step-status nuance to the UI that already renders it; remove the detail-modal double-render and dead wiring.

**Architecture:** Four sequenced, mostly-independent tasks. Three of the four are "stop discarding what already exists" (unify two briefing paths onto the structured builder; stop the backend collapsing step-status before the frontend icons receive it; stop discarding the computed trust-context). Only Task 2 adds new logic. Backend is Python/SQLAlchemy/Pydantic; frontend is Next.js/React/Zustand/vitest.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy async, pytest (custom `asyncio.run` hook — NO pytest-asyncio); TypeScript, React, vitest + @testing-library/react.

---

## Conventions (read once)

- **Backend gate:** `cd backend && uv run pytest tests/ --ignore=tests/e2e` (generous timeout ~2min). Single file: `uv run pytest tests/test_x.py -v`.
- **Backend test harness:** NO `asyncio_mode`; a custom `pytest_pyfunc_call` runs `async def test_*` via `asyncio.run`. Use `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`. Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.
- **Frontend gate:** `cd frontend && npm run lint && npm run build`. Tests: `npm test -- <path>` (vitest).
- **File-size check:** `python3 scripts/check_file_size.py <files>` (800-line Python cap / 400-line component cap) — the real pre-commit only runs ruff, so run this manually on touched files.
- **Ruff:** `cd backend && ruff check src/ tests/ && ruff format src/ tests/`.
- **Commits:** conventional (`feat`/`fix`/`refactor`/`test`/`docs`), **NO** `Co-Authored-By`. Tiny commits. In place on `rebuild/first-principles`. **Do not push/merge/deploy.**

---

## File Structure

**Backend (create/modify):**
- `src/services/surface_mapping.py` — add `build_briefing_preview()` (T1), `_plain_subtitle()` + harden `build_surface_preview_from_plan()` (T2).
- `src/services/surface_builder.py` — `_build_briefing_surface` calls shared helper (T1); attach `trust_context` on REST run surface (T4).
- `src/orchestrator/surface_pusher.py` — add `push_briefing_surface()` (T1).
- `src/orchestrator/jarvis.py` — add `_push_briefing_surface` facade; swap `generate_briefing` delivery (T1).
- `src/contracts/__init__.py` — widen `StepState.status` Literal; `step_status_to_ui` pass-through; comments (T3).

**Frontend (create/modify):**
- `src/components/workspace/surface-detail-modal.tsx` — phase-branch XOR tab-branch (T4).
- `src/lib/types/surfaces.ts` — drop 5 legacy kinds (T4).
- `src/lib/design-tokens.ts` — drop `execution` case (T4).
- `src/components/a2ui/components/__tests__/step-presentation.test.ts` — new: step-icon + isStepDone (T3).

**Tests touched:**
- `backend/tests/test_step_status_ui_mapping.py` — UPDATE `test_step3_verification_statuses_map_explicitly` (T3).
- `backend/tests/test_orchestrator_briefing_delivery.py` — UPDATE mock/assert for `_push_briefing_surface` (T1).
- `backend/tests/test_surface_mapping_briefing.py` — new (T1); `test_surface_mapping_harden.py` — new (T2); `test_surface_builder_trust_context.py` — new (T4).
- `frontend/src/lib/types/surfaces.test.ts` — UPDATE `table` case (T4).
- `frontend/src/components/workspace/surface-detail-modal.test.tsx` — new (T4).

---

## Task 1: Briefing card — one structured, deduped card

**Files:**
- Modify: `backend/src/services/surface_mapping.py` (add `build_briefing_preview`)
- Modify: `backend/src/services/surface_builder.py` (`_build_briefing_surface` uses helper)
- Modify: `backend/src/orchestrator/surface_pusher.py` (add `push_briefing_surface`)
- Modify: `backend/src/orchestrator/jarvis.py` (add `_push_briefing_surface` facade + swap in `generate_briefing`)
- Test: `backend/tests/test_surface_mapping_briefing.py` (new)
- Test: `backend/tests/test_orchestrator_briefing_delivery.py` (update)

- [ ] **Step 1: Write the failing test for `build_briefing_preview`**

Create `backend/tests/test_surface_mapping_briefing.py`:

```python
"""build_briefing_preview: one structured SurfacePreview from a Briefing row.

Guards the single-source-of-truth helper that both the REST rebuild
(_build_briefing_surface) and the live push (push_briefing_surface) call, so a
briefing card is never a markdown blob and never duplicated.
"""

from types import SimpleNamespace

from src.services.surface_mapping import build_briefing_preview


def _briefing(**kw):
    base = dict(
        briefing_id="brief_01",
        headline="Your Tuesday",
        top_priorities=[{"title": "Pay LIC premium"}, {"title": "Reply to investor"}],
        recommended_actions=[{"title": "Draft reply"}],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_populates_items_and_metrics_from_briefing():
    preview = build_briefing_preview(_briefing())
    assert preview.title == "Your Tuesday"
    assert preview.items == ["Pay LIC premium", "Reply to investor"]
    labels = {m.label: m.value for m in preview.metrics}
    assert labels == {"Priorities": "2", "Actions": "1"}
    assert preview.tags == ["briefing"]
    # subtitle is the first priority (plain), never a markdown blob.
    assert preview.subtitle == "Pay LIC premium"


def test_missing_headline_falls_back_to_daily_briefing():
    preview = build_briefing_preview(_briefing(headline=None, top_priorities=[], recommended_actions=[]))
    assert preview.title == "Daily Briefing"
    assert preview.items == []
    assert preview.subtitle is None
    assert {m.label: m.value for m in preview.metrics} == {"Priorities": "0", "Actions": "0"}


def test_priority_strings_and_dicts_both_supported():
    preview = build_briefing_preview(_briefing(top_priorities=["bare string", {"title": "dict one"}]))
    assert preview.items == ["bare string", "dict one"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_surface_mapping_briefing.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_briefing_preview'`.

- [ ] **Step 3: Implement `build_briefing_preview` in `surface_mapping.py`**

Add the `TYPE_CHECKING` import for `Briefing` and the helper. In the `if TYPE_CHECKING:` block add:

```python
    from src.models.briefings import Briefing
```

Add the function (after `derive_surface_kind`):

```python
def build_briefing_preview(briefing: "Briefing"):
    """Structured preview for a Briefing row — the single source of truth for a
    briefing card. Both the REST rebuild (SurfaceService._build_briefing_surface)
    and the live push (SurfacePusher.push_briefing_surface) call this so the two
    paths produce an identical, structured (never markdown-blob) card.

    items = priority titles (top 5); metrics = Priorities/Actions counts;
    subtitle = first priority (plain text, capped).
    """
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    priorities = briefing.top_priorities or []
    actions = briefing.recommended_actions or []

    def _priority_title(p) -> str:
        return (p.get("title", "") if isinstance(p, dict) else str(p)).strip()

    priority_titles = [t for t in (_priority_title(p) for p in priorities) if t]
    first_priority = priority_titles[0] if priority_titles else ""

    return SurfacePreview(
        title=briefing.headline or "Daily Briefing",
        subtitle=first_priority[:100] if first_priority else None,
        metrics=[
            SurfaceMetric(label="Priorities", value=str(len(priorities))),
            SurfaceMetric(label="Actions", value=str(len(actions))),
        ],
        items=priority_titles[:5],
        tags=["briefing"],
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd backend && uv run pytest tests/test_surface_mapping_briefing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Refactor `_build_briefing_surface` to call the helper (no behavior change)**

In `backend/src/services/surface_builder.py`, inside `_build_briefing_surface`, replace the inline preview-building block (the `priorities = ...` through the `preview = SurfacePreview(...)` assignment, currently ~lines 181-200) with:

```python
        from src.services.surface_mapping import build_briefing_preview

        surface_id = f"briefing_{briefing.briefing_id}"
        preview = build_briefing_preview(briefing)
        detail_config = build_detail_config("briefing", surface_id)
```

Remove the now-unused local `_priority_title`, `priorities`, `actions`, `priority_titles`, `first_priority` bindings. Keep the `return WorkspaceSurfacePush(...)` unchanged. If `SurfaceMetric`/`SurfacePreview` become unused in this file, leave the imports (they are used by other builders) — verify with ruff.

- [ ] **Step 6: Run the surface-builder tests + ruff**

Run: `cd backend && uv run pytest tests/test_surface_builder_active.py tests/test_briefing_surface_fallback.py -v && ruff check src/services/surface_builder.py src/services/surface_mapping.py`
Expected: PASS, no ruff errors. (Behavior of `_build_briefing_surface` is unchanged — same id, same structured preview.)

- [ ] **Step 7: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add backend/src/services/surface_mapping.py backend/src/services/surface_builder.py backend/tests/test_surface_mapping_briefing.py
git commit -m "refactor(a2ui): extract build_briefing_preview as single briefing-card source"
```

- [ ] **Step 8: Add `push_briefing_surface` to `SurfacePusher`**

In `backend/src/orchestrator/surface_pusher.py`, add a method (mirrors `push_workspace_surface` but briefing-specific, structured, deduped id):

```python
    async def push_briefing_surface(
        self,
        briefing,
        user_id: str,
        workspace_id: str,
    ) -> str | None:
        """Push a structured briefing surface, deduped with the REST rebuild.

        Uses surface_id = "briefing_<briefing_id>" (identical to
        SurfaceService._build_briefing_surface) so the live WS card and the REST
        card merge into one in the frontend store. Structured preview via
        build_briefing_preview — never the markdown-blob plan path.
        """
        from datetime import datetime, timedelta, timezone

        from src.contracts import WorkspaceSurfacePush
        from src.models.ids import ensure_prefix
        from src.services.surface_mapping import build_briefing_preview
        from src.ui.renderer import build_detail_config

        if not await self.check_surface_rate(user_id, "workspace"):
            logger.debug("Briefing surface push rate-limited for user %s", user_id)
            return None

        try:
            event_bus = await self._events.ensure_event_bus()
            if not event_bus:
                return None

            surface_id = ensure_prefix("briefing", briefing.briefing_id)
            preview = build_briefing_preview(briefing)
            detail_config = build_detail_config("briefing", surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind="briefing",
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                created_at=(
                    briefing.created_at.isoformat()
                    if getattr(briefing, "created_at", None)
                    else datetime.now(timezone.utc).isoformat()
                ),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface.model_dump(mode="json")})
            await event_bus.publish_to_channel(channel, ws_msg)

            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type="briefing",
                            payload=surface.model_dump(mode="json"),
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist briefing surface to DB", exc_info=True)
            return surface_id
        except Exception:
            logger.warning("Failed to push briefing surface", exc_info=True)
            return None
```

- [ ] **Step 9: Add the `_push_briefing_surface` facade in `jarvis.py`**

In `backend/src/orchestrator/jarvis.py`, next to `_push_workspace_surface` (~line 722), add:

```python
    async def _push_briefing_surface(
        self,
        briefing,
        user_id: str,
        workspace_id: str,
    ) -> str | None:
        """Delegate to SurfacePusher (facade kept for internal callers + test mockability)."""
        return await self._surfaces.push_briefing_surface(briefing, user_id, workspace_id)
```

- [ ] **Step 10: Swap `generate_briefing` delivery to the structured briefing push**

In `backend/src/orchestrator/jarvis.py`, inside `generate_briefing`'s delivery `try` block (~lines 597-624), replace the `await self._push_workspace_surface(PlanOutput(...))` call with a fetch-then-structured-push. Replace the block:

```python
                await self._push_workspace_surface(
                    PlanOutput(
                        goal="Daily Briefing",
                        reasoning=str(result)[:200],
                        steps=[
                            PlanStep(
                                description="Briefing update",
                                capability="system.add_to_brief",
                            )
                        ],
                    ),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    response_text=str(result)[:1000],
                )
```

with:

```python
                # The get_briefing tool wrote today's Briefing row mid-run (see the
                # idempotency note above), so fetch it and push a STRUCTURED briefing
                # surface deduped with the REST rebuild (same "briefing_<id>" id).
                # Never fall back to the markdown-blob plan push — if the row is
                # somehow absent, skip; the REST _build_briefing_surface still renders.
                from datetime import date as _date

                from sqlalchemy import select as _select

                from src.models.briefings import Briefing

                async with self._db_factory() as db:
                    row = await db.execute(
                        _select(Briefing)
                        .where(
                            Briefing.user_id == user_id,
                            Briefing.briefing_date == _date.today(),
                        )
                        .order_by(Briefing.created_at.desc())
                        .limit(1)
                    )
                    briefing_row = row.scalar_one_or_none()
                if briefing_row is not None:
                    await self._push_briefing_surface(
                        briefing_row, user_id=user_id, workspace_id=workspace_id
                    )
```

Note: `PlanOutput`/`PlanStep` may now be unused in this method — do NOT remove the module-level imports (they are used elsewhere in jarvis.py). Verify with ruff.

- [ ] **Step 11: Update the briefing-delivery test for the new push method**

In `backend/tests/test_orchestrator_briefing_delivery.py`:
- In `_build_orchestrator`, replace `orch._push_workspace_surface = AsyncMock()` with `orch._push_briefing_surface = AsyncMock()`.
- The `mock_db.execute` currently returns `existing_result` for every call. `generate_briefing` now does an extra `select(Briefing)` at delivery for the *first-run* path. Make the first-run mock return a Briefing row for that select so the push fires. Update the `existing_result` for `briefing_exists=False`: keep `scalar_one_or_none` returning `None` for the idempotency check, but the delivery select needs a row. Simplest: set `mock_db.execute` to an `AsyncMock` whose return value's `scalar_one_or_none` returns `None` first (idempotency) then a briefing row (delivery). Use `side_effect`:

```python
    idem_result = MagicMock()
    idem_result.scalar_one_or_none.return_value = (
        MagicMock(briefing_id="brief_existing") if briefing_exists else None
    )
    delivery_result = MagicMock()
    delivery_result.scalar_one_or_none.return_value = MagicMock(
        briefing_id="brief_new", created_at=None
    )
    # first execute() = idempotency check; subsequent = delivery Briefing fetch.
    mock_db.execute = AsyncMock(side_effect=[idem_result, delivery_result, delivery_result])
```

- Update assertions: where the test asserted `_push_workspace_surface` was called once on first delivery, assert `orch._push_briefing_surface.assert_awaited_once()` (and `assert_not_awaited()` on the already-delivered path). Keep the notifier assertions unchanged.

- [ ] **Step 12: Run the briefing-delivery + pusher tests**

Run: `cd backend && uv run pytest tests/test_orchestrator_briefing_delivery.py -v`
Expected: PASS. If `side_effect` list is exhausted (StopIteration), extend it with more `delivery_result` entries or use a callable side_effect — the number of `db.execute` calls depends on the delivery path; adjust the list length to match.

- [ ] **Step 13: Full gate + file-size + ruff, then commit**

Run: `cd backend && uv run pytest tests/ --ignore=tests/e2e -q && ruff check src/ && python3 scripts/check_file_size.py src/orchestrator/surface_pusher.py src/orchestrator/jarvis.py`
Expected: PASS; no file over cap.

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add backend/src/orchestrator/surface_pusher.py backend/src/orchestrator/jarvis.py backend/tests/test_orchestrator_briefing_delivery.py
git commit -m "fix(a2ui): deliver structured deduped briefing surface, drop markdown-blob push"
```

---

## Task 2: Harden generic plan preview (summary/plan/alert)

**Files:**
- Modify: `backend/src/services/surface_mapping.py` (`_plain_subtitle` + `build_surface_preview_from_plan`)
- Test: `backend/tests/test_surface_mapping_harden.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_surface_mapping_harden.py`:

```python
"""build_surface_preview_from_plan: subtitle is plain text (no markdown), and
entities are populated from the plan when present."""

from src.contracts import PlanOutput, PlanStep
from src.services.surface_mapping import _plain_subtitle, build_surface_preview_from_plan


def test_plain_subtitle_strips_markdown_syntax():
    assert _plain_subtitle("## Heading\n\n**bold** text\n---\n") == "Heading bold text"
    assert _plain_subtitle("- bullet `code` item") == "bullet code item"
    assert _plain_subtitle("") == ""
    assert _plain_subtitle(None) is None


def test_summary_preview_subtitle_is_plain():
    plan = PlanOutput(
        goal="Read inbox",
        reasoning="### Found\n**3** urgent emails",
        steps=[PlanStep(description="read", capability="email.read")],
    )
    preview = build_surface_preview_from_plan(plan, "summary", "Summary", "")
    assert "**" not in (preview.subtitle or "")
    assert "#" not in (preview.subtitle or "")
    assert preview.subtitle == "Found 3 urgent emails"


def test_entities_populated_from_plan_when_present():
    plan = PlanOutput(
        goal="Email Alice",
        reasoning="Send it",
        steps=[PlanStep(description="Email Alice about Q3", capability="email.send", entities=["Alice"])],
    )
    preview = build_surface_preview_from_plan(plan, "summary", "Summary", "")
    assert "Alice" in preview.entities
```

Note: verify `PlanStep` actually has an `entities` field before relying on it — run `cd backend && python -c "from src.contracts import PlanStep; print(PlanStep.model_fields.keys())"`. If `PlanStep` has NO entity-bearing field, drop `test_entities_populated_from_plan_when_present` and the entities logic in Step 3, leaving `entities=[]` (subtitle-hardening still applies). Do not invent a field.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_surface_mapping_harden.py -v`
Expected: FAIL — `cannot import name '_plain_subtitle'`.

- [ ] **Step 3: Implement `_plain_subtitle` + wire into `build_surface_preview_from_plan`**

In `backend/src/services/surface_mapping.py`, add near the top (after the regexes):

```python
_MD_STRIP_RE = re.compile(r"(\*\*|__|`|~~|^#{1,6}\s*|^>\s*|^[-*]\s+|-{3,})", re.MULTILINE)


def _plain_subtitle(text: str | None) -> str | None:
    """Reduce markdown-ish text to a plain one-line subtitle.

    Strips heading/emphasis/code/rule syntax and collapses whitespace so a
    surface subtitle is never a markdown blob. Returns None for falsy input.
    """
    if not text:
        return text
    cleaned = _MD_STRIP_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None
```

In `build_surface_preview_from_plan`, change:

```python
    subtitle = plan.reasoning[:120] if plan.reasoning else None
```

to:

```python
    subtitle = _plain_subtitle(plan.reasoning)
    if subtitle:
        subtitle = subtitle[:120]
```

And populate entities (ONLY if `PlanStep` has an entities field per Step 1 note). Change the `entities=[]` in the returned `SurfacePreview(...)` to:

```python
        entities=_entities_from_plan(plan),
```

adding the helper:

```python
def _entities_from_plan(plan: "PlanOutput", cap: int = 6) -> list[str]:
    """Collect distinct entity names referenced by plan steps (best-effort, capped)."""
    seen: list[str] = []
    for s in plan.steps:
        for name in getattr(s, "entities", None) or []:
            if name and name not in seen:
                seen.append(name)
            if len(seen) >= cap:
                return seen
    return seen
```

If `PlanStep` has no entities field, keep `entities=[]` and skip `_entities_from_plan`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_surface_mapping_harden.py -v`
Expected: PASS.

- [ ] **Step 5: Regression — existing surface-mapping callers unchanged for plain input**

Run: `cd backend && uv run pytest tests/ -k "surface_mapping or surface_builder or surface_push" -q && ruff check src/services/surface_mapping.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add backend/src/services/surface_mapping.py backend/tests/test_surface_mapping_harden.py
git commit -m "fix(a2ui): strip markdown from plan-preview subtitle, populate entities"
```

---

## Task 3: Propagate step-status nuance end-to-end

**Files:**
- Modify: `backend/src/contracts/__init__.py` (widen Literal, pass-through, comments)
- Modify: `backend/tests/test_step_status_ui_mapping.py` (flip the verification-status test)
- Create: `frontend/src/components/a2ui/components/__tests__/step-presentation.test.ts`

- [ ] **Step 1: Update the backend test to assert pass-through (RED)**

In `backend/tests/test_step_status_ui_mapping.py`, replace `test_step3_verification_statuses_map_explicitly` with:

```python
def test_step3_verification_statuses_pass_through():
    # The verification nuance now reaches the UI (frontend renders ✓? and ⚠),
    # so the backend no longer collapses these two — they pass through as-is.
    assert step_status_to_ui("completed_unverified") == "completed_unverified"
    assert step_status_to_ui("partially_completed") == "partially_completed"
    # And StepState must accept them (widened Literal).
    for s in ("completed_unverified", "partially_completed"):
        assert StepState(step_id="s", description="d", status=s).status == s
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_step_status_ui_mapping.py -v`
Expected: FAIL — `step_status_to_ui("completed_unverified")` returns `"completed"`, and/or `StepState(status="completed_unverified")` raises `ValidationError` (Literal rejects it).

- [ ] **Step 3: Widen the Literal + pass-through mapping + fix comments**

In `backend/src/contracts/__init__.py`:

Widen `StepState.status` (currently line ~364):

```python
    status: Literal[
        "pending",
        "executing",
        "completed",
        "completed_unverified",
        "partially_completed",
        "failed",
        "approval_needed",
        "user_action",
    ]
```

In `_STEP_STATUS_TO_UI`, change the two nuanced entries to pass-through and fix the comments:

```python
    # Verification nuance now reaches the UI (frontend step-presentation.tsx
    # renders ✓? for completed_unverified and ⚠ for partially_completed), so we
    # pass these through instead of collapsing to completed/failed.
    "completed_unverified": "completed_unverified",
    "partially_completed": "partially_completed",
```

Update the module comment above `_STEP_STATUS_TO_UI` if it claims the values are collapsed.

- [ ] **Step 4: Run backend step-status tests**

Run: `cd backend && uv run pytest tests/test_step_status_ui_mapping.py -v`
Expected: PASS. In particular `test_mapping_covers_every_db_step_status_and_yields_valid_ui_literal` still passes — the two now map to themselves, which the widened Literal accepts.

- [ ] **Step 5: Grep for consumers that switch on the OLD collapsed values**

Run: `cd backend && grep -rn "== \"completed\"\|== \"failed\"" src/services/execution_support.py src/services/surface_detail_builders/run.py`
Expected: inspect results. `isStepDone`/`TERMINAL_SUCCESS` logic is frontend; backend `TERMINAL_SUCCESS` (execution_state) uses DB statuses, not UI statuses, so it is unaffected. If any backend UI-status consumer assumed only `completed`/`failed`, it now also sees the two nuanced values — confirm none break (they are display-only). No code change expected; this is a verification step.

- [ ] **Step 6: Commit backend**

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add backend/src/contracts/__init__.py backend/tests/test_step_status_ui_mapping.py
git commit -m "feat(a2ui): propagate completed_unverified/partially_completed step status to UI"
```

- [ ] **Step 7: Write the frontend step-icon test (RED-ish; frontend already implements it)**

Create `frontend/src/components/a2ui/components/__tests__/step-presentation.test.ts`:

```typescript
import { test, expect } from "vitest";
import { stepStatusIcon } from "../step-presentation";
import { isStepDone } from "@/lib/a2ui-types";

test("completed_unverified renders the sent-but-unconfirmed icon", () => {
  expect(stepStatusIcon("completed_unverified").icon).toBe("✓?");
});

test("partially_completed renders the read-back-contradicted icon", () => {
  expect(stepStatusIcon("partially_completed").icon).toBe("⚠");
});

test("completed renders a plain check", () => {
  expect(stepStatusIcon("completed").icon).toBe("✓");
});

test("isStepDone: completed_unverified is done, partially_completed is not", () => {
  expect(isStepDone("completed_unverified")).toBe(true);
  expect(isStepDone("partially_completed")).toBe(false);
  expect(isStepDone("completed")).toBe(true);
});
```

- [ ] **Step 8: Run the frontend test**

Run: `cd frontend && npm test -- src/components/a2ui/components/__tests__/step-presentation.test.ts`
Expected: PASS immediately — the frontend already implements these icons and `isStepDone` semantics; this test locks the contract now that the backend actually ships the values.

- [ ] **Step 9: Frontend gate + commit**

Run: `cd frontend && npm run lint && npm run build`
Expected: PASS.

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add frontend/src/components/a2ui/components/__tests__/step-presentation.test.ts
git commit -m "test(a2ui): lock step-status icon contract for verification nuance"
```

---

## Task 4: Fix detail-modal double-render + dead wiring

### Task 4a: Detail modal — live exec surface XOR detail tabs

**Files:**
- Modify: `frontend/src/components/workspace/surface-detail-modal.tsx`
- Test: `frontend/src/components/workspace/surface-detail-modal.test.tsx` (new)

- [ ] **Step 1: Write the failing render test**

Create `frontend/src/components/workspace/surface-detail-modal.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { test, expect, vi, beforeEach } from "vitest";
import { SurfaceDetailModal } from "./surface-detail-modal";
import type { WorkspaceSurface } from "@/stores/surface-store";
import { fetchSurfaceDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchSurfaceDetail: vi.fn().mockResolvedValue({
    tab_id: "steps",
    sections: [{ id: "s1", title: "TAB_STEPS_SECTION", collapsed: false, children: [] }],
  }),
}));

function runSurface(): WorkspaceSurface {
  return {
    id: "run_01",
    kind: "run",
    preview: {
      title: "Live run goal",
      subtitle: null, status: "running", priority: null, metrics: [],
      entities: [], progress: null, timestamp: null, tags: [],
    },
    detail_config: { tabs: [{ id: "steps", label: "Steps", endpoint: "/x" }], default_tab: "steps" },
    // live execution fields present ⇒ phase set ⇒ live surface should win.
    phase: "executing",
    steps: [], current_step: null, progress: "", approval: null, results: null,
  } as unknown as WorkspaceSurface;
}

function renderModal(surface: WorkspaceSurface) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <SurfaceDetailModal surface={surface} open={true} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.mocked(fetchSurfaceDetail).mockClear());

test("when phase is set, live exec surface renders and detail tabs do NOT", () => {
  renderModal(runSurface());
  // Live exec surface shows the goal title.
  expect(screen.getAllByText("Live run goal").length).toBeGreaterThan(0);
  // The DB-derived tab section must NOT render (no double step list).
  expect(screen.queryByText("TAB_STEPS_SECTION")).toBeNull();
  // And no tab fetch happens (tabs suppressed when live).
  expect(fetchSurfaceDetail).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test -- src/components/workspace/surface-detail-modal.test.tsx`
Expected: FAIL — `fetchSurfaceDetail` IS called and/or `TAB_STEPS_SECTION` renders (current code renders both tab content and exec surface).

- [ ] **Step 3: Make the branches mutually exclusive**

In `frontend/src/components/workspace/surface-detail-modal.tsx`, in the derived-values block (~lines 61-66), introduce `showLiveExec` and fold it into `tabs`/`defaultTabId`:

```typescript
  const presenterSections = surface.surface_data?.sections ?? [];
  const hasPresenterContent = presenterSections.length > 0;
  // Live execution surface takes precedence over DB-derived detail tabs — a run
  // with a live phase renders its own step list, so suppress the tabs to avoid a
  // double step list in one pane.
  const showLiveExec = !hasPresenterContent && !!surface.phase;
  const tabs = hasPresenterContent || showLiveExec ? [] : (surface.detail_config?.tabs ?? []);
  const defaultTabId =
    hasPresenterContent || showLiveExec
      ? null
      : (surface.detail_config?.default_tab ?? tabs[0]?.id ?? null);
```

Because `tabs` is now `[]` and `defaultTabId` is `null` when `showLiveExec`, `activeTabId` starts null, the fetch effect early-returns (no `fetchSurfaceDetail`), and the tab-content branches (`loading`/`error`/`activeData`) are inert. Then change the exec-surface guard (~line 245) from:

```typescript
          {surface.phase && (
```

to:

```typescript
          {showLiveExec && (
```

And gate the empty-state so it doesn't show under the live surface (~line 265):

```typescript
          {!hasPresenterContent && !showLiveExec && !loading && !error && !activeData && tabs.length === 0 && (
```

- [ ] **Step 4: Run the render test**

Run: `cd frontend && npm test -- src/components/workspace/surface-detail-modal.test.tsx`
Expected: PASS.

- [ ] **Step 5: Guard the non-live path still shows tabs**

Add a second test to the same file (a run surface with NO `phase` still fetches + shows the tab section):

```typescript
test("when phase is absent, detail tabs render and fetch", async () => {
  const s = runSurface();
  const noPhase = { ...s, phase: null } as unknown as WorkspaceSurface;
  renderModal(noPhase);
  expect(await screen.findByText("TAB_STEPS_SECTION")).toBeTruthy();
  expect(fetchSurfaceDetail).toHaveBeenCalledWith("run_01", "steps");
});
```

Run: `cd frontend && npm test -- src/components/workspace/surface-detail-modal.test.tsx`
Expected: PASS (both tests).

- [ ] **Step 6: Frontend gate + commit**

Run: `cd frontend && npm run lint && npm run build`

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add frontend/src/components/workspace/surface-detail-modal.tsx frontend/src/components/workspace/surface-detail-modal.test.tsx
git commit -m "fix(a2ui): render live exec surface XOR detail tabs, kill double step list"
```

### Task 4b: Populate trust_context on the REST path

**Files:**
- Modify: `backend/src/services/surface_builder.py`
- Test: `backend/tests/test_surface_builder_trust_context.py` (new)

- [ ] **Step 1: Write the failing test**

First inspect the current signature and callers of `_approval_risk_and_flags` (surface_builder.py ~line 109) and how the run `WorkspaceSurfacePush` is built (~line 295-331). Then create `backend/tests/test_surface_builder_trust_context.py`. Because `_approval_risk_and_flags` and `_get_trust_context` are DB-driven, test at the seam that matters: an awaiting-approval run surface carries a non-null `trust_context`. Use the real-DB harness pattern (self-contained `_db_reachable` skip guard + seed User→Workspace→TaskRun→Approval→TrustState), OR — if that is heavy — unit-test a new pure helper. Prefer the pure-helper route:

Add a test asserting a new method `_run_trust_context(run_id)` returns the dict from `_get_trust_context` (the value currently discarded):

```python
"""SurfaceService attaches the computed trust_context to awaiting-approval run
surfaces on the REST path (previously computed then discarded)."""

from unittest.mock import AsyncMock, MagicMock

from src.services.surface_builder import SurfaceService


def test_run_trust_context_returns_computed_dict():
    svc = SurfaceService(db=MagicMock(), workspace_id="ws_1")
    approval = MagicMock(artifact_refs={"tool_name": "email.send"}, risk_level="low")
    svc._latest_pending_approval = AsyncMock(return_value=approval)
    svc._get_trust_context = AsyncMock(return_value={"trust_level": "learning", "label": "Similar to 4 approvals"})

    async def _run():
        return await svc._run_trust_context("run_01")

    import asyncio

    result = asyncio.run(_run())
    assert result == {"trust_level": "learning", "label": "Similar to 4 approvals"}
```

Note: this assumes a small refactor extracting the pending-approval lookup (currently inline in `_approval_risk_and_flags`) into `_latest_pending_approval(run_id)`. If you prefer not to refactor, adapt the test to patch the inline query. Verify the actual method names against the file before finalizing.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_surface_builder_trust_context.py -v`
Expected: FAIL — `_run_trust_context` / `_latest_pending_approval` do not exist.

- [ ] **Step 3: Implement — extract the pending-approval lookup + add `_run_trust_context`, attach to the surface**

In `backend/src/services/surface_builder.py`:

Extract the pending-Approval query from `_approval_risk_and_flags` into:

```python
    async def _latest_pending_approval(self, run_id: str):
        """Most recent pending Approval for a run (or None)."""
        from src.models.approvals import Approval

        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.run_id == run_id,
                Approval.workspace_id == self._workspace_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
```

Rewrite `_approval_risk_and_flags` to call `_latest_pending_approval`. Add:

```python
    async def _run_trust_context(self, run_id: str) -> dict[str, str] | None:
        """Full trust-context dict for an awaiting-approval run (was discarded)."""
        approval = await self._latest_pending_approval(run_id)
        if not approval:
            return None
        return await self._get_trust_context(approval)
```

In `_build_run_surfaces`, where `awaiting` is true and risk/flags are resolved (~line 292-293), also resolve and attach the trust context:

```python
            trust_context = None
            if awaiting:
                risk_value, flags = await self._approval_risk_and_flags(run.run_id)
                trust_context = await self._run_trust_context(run.run_id)
```

and pass `trust_context=trust_context` to the `WorkspaceSurfacePush(...)` constructor for the run surface. (`WorkspaceSurfacePush.trust_context` already exists — `contracts/__init__.py:261`.) To avoid two pending-approval lookups, optionally have `_approval_risk_and_flags` return the approval too and reuse it — acceptable either way; correctness first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_surface_builder_trust_context.py tests/test_surface_builder_active.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + ruff + commit**

Run: `cd backend && uv run pytest tests/ --ignore=tests/e2e -q && ruff check src/services/surface_builder.py`

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add backend/src/services/surface_builder.py backend/tests/test_surface_builder_trust_context.py
git commit -m "fix(a2ui): attach computed trust_context to REST run surfaces"
```

### Task 4c: Drop legacy frontend surface kinds

**Files:**
- Modify: `frontend/src/lib/types/surfaces.ts`
- Modify: `frontend/src/lib/design-tokens.ts`
- Modify: `frontend/src/lib/types/surfaces.test.ts` (update the `table` case)

- [ ] **Step 1: Update `surfaces.test.ts` to expect the removed kinds to degrade (RED)**

In `frontend/src/lib/types/surfaces.test.ts`, the first test asserts `normalizeSurfaceKind("table", "srf_1")` is `"table"`. Change it so the removed kinds now degrade to `summary` with a warning:

```typescript
test("known kind passes through unchanged", () => {
  expect(normalizeSurfaceKind("briefing", "srf_1")).toBe("briefing");
  expect(normalizeSurfaceKind("run", "srf_1")).toBe("run");
});

test("removed legacy kinds now degrade to summary with a warning", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  for (const k of ["checklist", "comparison", "timeline", "table", "activity"]) {
    expect(normalizeSurfaceKind(k, "srf_1")).toBe("summary");
  }
  expect(warn).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test -- src/lib/types/surfaces.test.ts`
Expected: FAIL — `normalizeSurfaceKind("table", …)` still returns `"table"`.

- [ ] **Step 3: Remove the legacy kinds**

In `frontend/src/lib/types/surfaces.ts`:
- Remove `checklist`, `comparison`, `timeline`, `table`, `activity` from the `SurfaceKind` union (keep `plan`, `approval`).
- Remove the same five string literals from the `ALL_SURFACE_KINDS` set.

In `frontend/src/lib/design-tokens.ts`, remove the `case "execution":` branch (~line 216); it falls through to the default surface color.

- [ ] **Step 4: Run the test + typecheck**

Run: `cd frontend && npm test -- src/lib/types/surfaces.test.ts && npm run build`
Expected: PASS. The `build` (tsc) confirms no other code references the removed kinds. If a reference exists, it will be a type error — fix that call site to use a surviving kind (likely `summary`).

- [ ] **Step 5: Lint + commit**

Run: `cd frontend && npm run lint`

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add frontend/src/lib/types/surfaces.ts frontend/src/lib/design-tokens.ts frontend/src/lib/types/surfaces.test.ts
git commit -m "chore(a2ui): drop legacy frontend surface kinds the backend never emits"
```

---

## Final verification

- [ ] **Backend full gate:** `cd backend && uv run pytest tests/ --ignore=tests/e2e -q` → all green.
- [ ] **Backend lint/format:** `cd backend && ruff check src/ tests/ && ruff format --check src/ tests/`.
- [ ] **Backend file size:** `cd backend && python3 scripts/check_file_size.py src/orchestrator/surface_pusher.py src/orchestrator/jarvis.py src/services/surface_builder.py src/services/surface_mapping.py` → none over 800.
- [ ] **Frontend gate:** `cd frontend && npm run lint && npm run build && npm test` → all green.
- [ ] **Manual data check (optional, infra up):** trigger a briefing and confirm `ui_surfaces` has exactly one `briefing_<id>` row with populated `preview.items`/`metrics` and NO `surf_<ulid>` blob:
  `docker exec jarvis-postgres-1 psql -U jarvis -d jarvis -c "SELECT surface_id, preview->'items', preview->'metrics' FROM ui_surfaces WHERE surface_type='briefing';"`
- [ ] Confirm no `Co-Authored-By` in any commit: `git log --format='%an %s' rebuild/first-principles -8`.

## Spec coverage map

- Spec Issue #1a (briefing blob + duplication) → Task 1.
- Spec Issue #1b (generic preview markdown/entities) → Task 2.
- Spec Issue #2 (step-status collapse) → Task 3.
- Spec Issue #3 (detail-modal double-render) → Task 4a.
- Spec Issue #4a (trust_context discarded) → Task 4b.
- Spec Issue #4b (legacy frontend kinds) → Task 4c.
