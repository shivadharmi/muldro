# A2UI Surface Fixes — Design Spec

**Date:** 2026-07-22
**Branch:** `rebuild/first-principles`
**Status:** Approved design → ready for implementation plan
**Scope:** Subsystem B (A2UI surface DTO / preview-data layer). One spec, four sequenced tasks. The A2UI *component* layer (backend `ComponentType` ↔ frontend `renderer.tsx`, all 17 types) is healthy and out of scope — the defects live in preview-data population and the run/summary DTO layer.

---

## 1. Problem (code + data proven)

Four independent defects in how persisted/pushed surfaces carry data. All verified against current code and live `ui_surfaces` rows on 2026-07-22.

### Issue #1a — Briefing surface is a markdown blob (two unreconciled paths render one briefing)

There is **one** `Briefing` row per (user, day), but **two** independent code paths turn it into a workspace card, and they never dedupe:

- **Live delivery (WS push):** `jarvis.py::generate_briefing` (~:608) fires `push_workspace_surface(PlanOutput(goal="Daily Briefing", reasoning=str(result)[:200], …))`. That generic helper (`surface_mapping.build_surface_preview_from_plan`) can't know briefing structure, so it dumps raw markdown into `subtitle` and leaves `items`/`metrics`/`entities` empty. Card id = `surf_<ulid>`.
- **Durable reconstruction (REST fetch):** `SurfaceService._build_briefing_surface` runs on every `GET /v1/workspace/surfaces` and rebuilds a **structured** card from the `Briefing` row (`items` = priority titles, `metrics` = Priorities/Actions). Card id = `briefing_<briefing_id>`.

Because the ids differ, the frontend store can't merge them — the workspace shows the good card **and** the blob card. Live-proven blob in DB: `surf_01KY0E147B3R8MGH34H3MNY04A` has `items=[]`, `metrics=[]`, `entities=[]`, and `subtitle="Good morning. Here's your briefing for **Tuesday, July 21, 2026** — 5 things need your attention today.\n\n---\n\n### 🔴 Urge…"`.

This is strangler-fig drift: the generic live-push was a fast way to get *a* card up without writing a briefing-shaped push; the structured REST rebuild is the newer two-layer surface model. Neither is wrong alone — the bug is that they were never unified.

### Issue #1b — Generic plan preview leaks markdown into subtitle (milder)

`build_surface_preview_from_plan` (used for `summary`/`plan`/`alert` pushes) sets `subtitle = plan.reasoning[:120]` and always leaves `entities=[]`. For non-briefing kinds the reasoning is usually a plain sentence, so this is far milder than 1a — but heading/emphasis syntax can still leak, and `entities` is never populated even when the plan names them.

### Issue #2 — Step-status nuance is collapsed at the backend boundary (frontend already built for it)

A `TaskStep` can terminate in `completed_unverified` (write fired, read-back unconfirmed — "sent, unconfirmed") or `partially_completed` (read-back **contradicted** the expected effect). These are outputs of the rebuild's verification subsystem.

- **Backend collapses them:** `contracts.step_status_to_ui` maps `completed_unverified→completed` and `partially_completed→failed`; the backend `StepState.status` `Literal` (`contracts/__init__.py:364`) doesn't even allow the nuanced values.
- **Frontend is fully ready and receiving nothing:** `step-presentation.tsx::stepStatusIcon` renders `✓?` for `completed_unverified` (:39) and `⚠` for `partially_completed` (:42); the frontend `StepState` union (`a2ui-types.ts:186-187`) includes both; `isStepDone` (`STEP_TERMINAL_SUCCESS`) treats `completed_unverified` as done, `partially_completed` as not-done.
- **Stale docs:** the backend comments (and CLAUDE.md) claim the frontend renders the `✓?`/`⚠` icons "from the raw nuance" — false today, because the nuance never leaves the backend.

Both StepState build paths funnel through the same function: the **live** autonomous path (`dag_runner.py` → `execution_support._step_to_state` → `step_status_to_ui`) and the **persisted** run-detail Steps tab (`surface_detail_builders/run.py:50` → `step_status_to_ui`). So one backend change reaches both surfaces.

### Issue #3 — Detail modal double-renders steps for run/summary

`surface-detail-modal.tsx` renders the DB-derived tab sections (`activeData.sections`, :222-242) **and**, in a separate non-exclusive branch, the live `A2UIExecutionSurface` whenever `surface.phase` is set (:245-263). A run/summary surface with a persisted `phase` **and** a Steps tab shows **two** step lists in one pane.

### Issue #4 — Dead wiring

- **4a `trust_context` discarded on REST path.** `SurfaceService._get_trust_context` computes a full trust-context dict (`label`/`variant`/`graduation_hint`), but `_approval_risk_and_flags` extracts only `trust_level` into `flags` and drops the rest. The `WorkspaceSurfacePush.trust_context` field (`contracts/__init__.py:261`, `dict[str,str] | None`) is populated on the WS/chat path but **never** on the REST path, though the frontend store + card can render it.
- **4b Legacy frontend surface kinds the backend never emits.** Frontend `SurfaceKind` (`lib/types/surfaces.ts`) carries `checklist`, `comparison`, `timeline`, `table`, `activity` + an `execution` color case in `design-tokens.ts:216`. Backend `SurfaceKind` Literal emits none of these. (`plan`/`approval` ARE still backend-emitted — keep them.)

## 2. Goals & non-goals

**Goal:** persisted and pushed surfaces carry structured, typed, non-duplicated data; the verification step-status nuance reaches the UI that already renders it; remove double-render and dead wiring.

**In scope:** the four tasks below.

**Out of scope (explicit):**
- No redesign of the A2UI component tree or `renderer.tsx` (healthy).
- No change to Presenter `message`/`summary` surfaces' "thin preview + rich `surface_data` in modal" model — that is acceptable design (content lives in the detail modal, not lost).
- No backfill of existing blob rows in `ui_surfaces` (24h TTL — they age out; the read-side fixes self-heal on next generation).
- No new detail tabs, no new surface kinds.

## 3. Design — four tasks

### Task 1 — Briefing card: one structured, deduped card (backend)

Extract the briefing→preview logic (currently inline in `SurfaceService._build_briefing_surface`) into one shared helper so both delivery paths build the identical card:

```python
# surface_mapping.py (or a small surface_previews.py leaf)
def build_briefing_preview(briefing: Briefing) -> SurfacePreview:
    """Structured preview for a Briefing row: items=priority titles (top 5),
    metrics=[Priorities, Actions]. Single source of truth for a briefing card."""
```

- `_build_briefing_surface` calls the helper (behavior unchanged; still id `briefing_<briefing_id>`).
- Add `SurfacePusher.push_briefing_surface(briefing, user_id, workspace_id)` that builds the surface with **`surface_id = ensure_prefix("briefing", briefing.briefing_id)`** and the same helper, then does the existing WS publish + `ui_surfaces` persist.
- `generate_briefing` delivery replaces the `push_workspace_surface(PlanOutput(...))` call with `push_briefing_surface(briefing, …)`. It must pass the actual `Briefing` row (fetch it after generation; the row is written by the briefing pipeline).

**Result:** one structured card, delivered live over WS **and** durable via REST, same id on both paths → frontend store merges into one. Blob gone.

**Edge:** if the `Briefing` row isn't available at delivery time (defensive), skip the push (the REST `_build_briefing_surface` still renders it on next fetch) — never fall back to the markdown-blob push.

### Task 2 — Harden generic plan preview (backend)

In `build_surface_preview_from_plan`:
- Strip markdown/heading/emphasis syntax from `subtitle` (leading `#`/`>`/`-`, `**`, `` ` ``, horizontal rules `---`) via a small pure `_plain_subtitle(text) -> str` helper, then truncate to 120.
- Populate `entities[]` from plan steps where cheaply available (e.g. entity names referenced on steps) — best-effort, capped, empty when none.
- Scope: `summary`/`plan`/`alert` kinds. Briefing no longer flows through this path after Task 1.

### Task 3 — Propagate step-status nuance end-to-end (cross-stack contract)

Centralized backend change; frontend already renders it.

- **Widen** `StepState.status` Literal (`contracts/__init__.py:364`) to add `"completed_unverified"`, `"partially_completed"`.
- **Change** `step_status_to_ui`: map `completed_unverified→completed_unverified` and `partially_completed→partially_completed` (pass-through). Keep the collapse for all other richer DB statuses (`ready→pending`, `blocked→pending`, `timed_out→failed`, `cancelled→failed`, `skipped→completed`, `waiting_approval→approval_needed`, `awaiting_input→user_action`). Keep the `pending` fallback for unknown/empty.
- **Fix** the now-accurate comments in `contracts/__init__.py` and the CLAUDE.md claim (icons ARE rendered, and the nuance now actually reaches the frontend).
- **Reach:** because `dag_runner._step_to_state` (live autonomous surface) and `surface_detail_builders/run.py` (persisted Steps tab) both call `step_status_to_ui`, this single change lights up `✓?`/`⚠` in **both** the live execution surface and the run-detail modal.
- **Frontend:** no code change required. Add a frontend test asserting `stepStatusIcon("completed_unverified") → ✓?` and `stepStatusIcon("partially_completed") → ⚠`, and that `isStepDone` treats them correctly — since these statuses will now actually arrive.

### Task 4 — Fix double-render + dead wiring

**4.0 (#3) double-render** — `surface-detail-modal.tsx`: make the live exec surface and the DB tab sections mutually exclusive. When `surface.phase` is set, render `A2UIExecutionSurface` **instead of** the tab-section branch (the live surface already shows steps); otherwise render tabs as today. Concretely: gate the `!hasPresenterContent && activeData` tab-content branch on `!surface.phase`, and keep the `A2UIExecutionSurface` block as the phase branch. Presenter-content branch is unaffected.

**4a (#4a) trust_context on REST path** — in `SurfaceService`, when a run is `awaiting_approval`, attach the full `trust_context` dict (already computed by `_get_trust_context`) to the `WorkspaceSurfacePush`. Refactor `_approval_risk_and_flags` (or a sibling) to return the dict alongside risk/flags rather than discarding it, and set `trust_context=…` on the run `WorkspaceSurfacePush`. Frontend already stores/renders it.

**4b (#4b) legacy kinds** — remove `checklist`, `comparison`, `timeline`, `table`, `activity` from the frontend `SurfaceKind` union, `ALL_SURFACE_KINDS`, and the `execution` case in `design-tokens.ts`. Keep `plan`/`approval`. `normalizeSurfaceKind` still degrades unknown kinds to `summary` with a warning, so any stale persisted row of a removed kind renders safely.

## 4. Testing

- **TDD per task**, real contracts (no mocks that mask the `StepState` Literal — Task 3's whole point is the Literal boundary).
- **Task 1:** test both call sites build id `briefing_<id>` with populated `items`/`metrics`; test the blob push is gone (no `surf_` briefing surface from `generate_briefing`).
- **Task 2:** test `_plain_subtitle` strips `**`/`###`/`---`; test `entities` populated from a plan.
- **Task 3:** backend test `step_status_to_ui` passes the two through + Literal accepts them; test `_step_to_state` and the run detail-tab builder both emit the nuanced status; frontend test for the two icons.
- **Task 4:** frontend test detail modal renders exactly one step list when `phase` set (no tab double); backend test `trust_context` present on an awaiting-approval REST run surface; frontend type test that removed kinds are gone and `normalizeSurfaceKind` still degrades safely.
- **Gate between tasks:** `cd backend && uv run pytest tests/ --ignore=tests/e2e` (generous timeout) + `cd frontend && npm run lint && npm run build`. Run `python3 scripts/check_file_size.py` on touched Python (800 cap) / components (400).
- Tiny conventional commits, **no** `Co-Authored-By`, in place on `rebuild/first-principles`. Do not push/merge/deploy.

## 5. File touch map (anticipated)

- `backend/src/services/surface_mapping.py` — `build_briefing_preview`, `_plain_subtitle`, harden `build_surface_preview_from_plan` (T1, T2)
- `backend/src/services/surface_builder.py` — call shared helper; attach `trust_context` on REST run surface (T1, T4a)
- `backend/src/orchestrator/surface_pusher.py` — `push_briefing_surface` (T1)
- `backend/src/orchestrator/jarvis.py` — `generate_briefing` delivery swap (T1)
- `backend/src/contracts/__init__.py` — widen `StepState.status` Literal; fix `step_status_to_ui`; comments (T3)
- `backend/CLAUDE.md` / doc comments — correct the step-icon claim (T3)
- `frontend/src/components/workspace/surface-detail-modal.tsx` — mutually-exclusive phase/tab branches (T3-fe test, T4)
- `frontend/src/components/a2ui/components/__tests__/` — step-icon test (T3)
- `frontend/src/lib/types/surfaces.ts`, `frontend/src/lib/design-tokens.ts` — drop legacy kinds (T4b)
