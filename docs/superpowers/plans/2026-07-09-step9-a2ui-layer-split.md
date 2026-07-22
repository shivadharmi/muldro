# Step 9 — A2UI Render-Payload Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Step 9 of the first-principles rebuild as a **LIVE, runtime-agnostic cleanup of the shared A2UI declarative render payload** — prune provably-dead component types + surface kinds, route flattened narrative to a markdown component, and de-duplicate the approval render surface — WITHOUT building the native-stream→surface_update adapter (deferred to Step 10), WITHOUT touching the `SurfaceUpdate` phase machine (deleted post-Step-10), WITHOUT renaming the layer (deferred to the standards track), and WITHOUT a migration.

**Architecture:** The A2UI system has TWO disjoint frontend consumption paths and this plan touches only the SHARED declarative render payload common to both. (1) The **autonomous path** emits the `SurfaceUpdate` phase machine (`plan_ready → executing → approval_needed → completed/failed`) from `graph_executor.py`/`dag_runner.py`/`trust_gate.py` → `execution_surface_emitter.py` → Redis `jarvis:a2ui:{user_id}` → WebSocket relay (`routes_ws.py`) → `execution-surface.tsx`; this path is **entirely legacy until Step 10** and is NOT touched here. (2) The **deep chat path** streams inline ` ```json:surface ` blocks (`surface_mapping.py`) + `WorkspaceSurfacePush`, never a `SurfaceUpdate`. Both paths render the same component tree (`src/ui/contracts.py` types → `src/ui/renderer.py` builders → `frontend/.../renderer.tsx` switch), which is the surface this plan cleans up. Because it is a shared seam with no runtime-specific behavior, Step 9 is LIVE + runtime-agnostic (the Steps 2/4/8-cleanup category), gated by characterization tests on both stacks — NOT flag-dormancy.

**Tech Stack:** Python 3.13 (backend: `src/ui/`, `src/services/surface_detail_builders/`, `src/contracts/`), Next.js 16 / React 19 / TypeScript / Zustand (frontend: `frontend/src/components/a2ui/`), `react-markdown` ^10.1.0 + `remark-gfm` (already deps). No new backend deps, no migration.

---

## Baseline (verified at plan-open, HEAD `1f343cc`; skeleton at `ef33e9f`)

- Branch `rebuild/first-principles` (off `main`, NOT pushed). Backend: **3325 passed / 18 skipped** (`uv run pytest tests/ --ignore=tests/e2e`, ~180s). Single alembic head `1a2770a28c39`, `alembic check` drift-free, `ruff check src tests` clean.
- Frontend (from `frontend/`): `npm run lint` = 0 errors (2 pre-existing warnings), `npm run build` = green.
- **INFRA GOTCHA:** `:6379` may be served by `hyperlocal-redis` OR `jarvis-redis-1`; either fine if published (`docker start hyperlocal-redis` if refused). A backend gate with **~108 skipped = redis/postgres DOWN = NOT green** — restore infra first (`docker compose up -d postgres redis qdrant`).
- **uv NO pip** — `uv sync --all-extras`; run tests via `uv run pytest …`. Custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio). Do NOT edit backend/ while a `uvicorn --reload` worker runs.

## Forks resolved with user (one-by-one, prose — NOT batched)

1. **(LOAD-BEARING) dual-runtime surface pipeline** → the native-stream→`surface_update` **adapter is a Step-10 concern, built here NOT at all** (ledger Category B): its only phase-producer is the autonomous DAG, legacy until Step 10; a deep chat turn has no phases to translate. The **phase machine is untouched** (deletion stays post-Step-10). Everything else is on the **shared declarative render payload = LIVE + runtime-agnostic**, gated by tests (no flag engineers dormancy because nothing here is runtime-specific). Approval work **bounded** to the live, non-phase-machine surface.
2. **`version` field** → **TTL-prune, no version bump**; keep `version` as a documented reserved forward-compat hook. No render-branch (it would have a single live arm; `version` is triply-dead: untyped on FE, absent from the mainline `WorkspaceSurfacePush` envelope, renders nothing). The existing 24h read-filter + 48h hard-delete TTL (`eviction_service.py:196`) absorbs any old-schema rows across the prune.
3. **rename / standards** → **defer BOTH** the AG-UI/MCP-Apps standards adoption (a separate larger track, `project_week3_standards_adoption`) AND the mechanical A2UI→SurfaceKit rename (its purpose — free the name — only bites when the standards layer is built; renaming now = triple churn across Step-9/Step-10/standards). Keep "A2UI" for now.
4. **packaging** → **ONE plan, 5 phases, NO P0 spike** (no unproven offline assumption remains; the adapter unknown was disproven-as-out-of-scope by extraction, not something to prove). Shared hot files force single-owner sequencing → one plan.

## Extraction findings (4 parallel passes, cross-verified @ `ef33e9f`; verify-don't-trust catches folded)

**Grounded facts driving the tasks (file:line):**

- **`SurfaceUpdate` is in `src/contracts/__init__.py:450`** (NOT `src/ui/contracts.py` — CLAUDE.md stale), `frozen=True`. Phase Literal declares 7 (`:461-463`) but only 5 are emitted: `plan_ready` (`graph_executor.py:316`), `executing` (`dag_runner.py:218`), `approval_needed` (`trust_gate.py:173`), `completed` (`dag_runner.py:159`), `failed` (`dag_runner.py:186,771`). `planning`/`partial` are dead enum arms. **NOT touched by Step 9** (deferred).
- **`version` lives only on `A2UISurface`/`A2UIComponent`** (`src/ui/contracts.py:159,125`, default `A2UI_SCHEMA_VERSION=1` at `:26`). NOT on `SurfaceUpdate`, NOT on `WorkspaceSurfacePush`, NOT a `ui_surfaces` column (migration `0d5070b8e434:769-782`). The mainline path uses `WorkspaceSurfacePush`; the versioned `A2UISurface` envelope has **exactly ONE producer** — `reauth_service.py:341`.
- **13 dead ComponentTypes** (0 live call-sites, census-verified): `Column, Tabs, Modal, TextField, Select, Toggle, Form, DataGrid, Chart, Avatar, StatusIndicator, KanbanBoard, Calendar`. **16 live**: `Text, Badge, Row, Card, Metric, Button, Alert, List, Table, Timeline, MemoryCard, Divider, CodeBlock, Progress, EntityCard, ExecutionTrace`. (Spec's "10 dead" is WRONG — it's 13.)
- **`ComponentType` enum** `src/ui/contracts.py:79-114`; **builders** `src/ui/renderer.py`; `A2UIComponent._validate_properties` (`contracts.py:147-154`) consults `PROPERTY_MODELS` from `src/ui/component_properties.py` → dead-type prune must also clean that registry.
- **Frontend renderer** `frontend/src/components/a2ui/renderer.tsx`: 29 switch cases (`:86-164`) + 29 imports (`:5-33`) map 1:1 to the 29 backend enum values; component files in `frontend/src/components/a2ui/components/`. Frontend consumes `version` **nowhere** (grep-0; `a2ui-types.ts:8-21` doesn't even declare it).
- **SurfaceKind Literal** `src/ui/contracts.py:30-49`. Of the 7 "legacy" kinds (`:42-48`): `plan` is **STILL LIVE** (`derive_surface_kind` emits it, `surface_mapping.py:42,46`) — KEEP; `approval` is **demoted-to-inline** (run-surface tab; detail builders still reachable for old rows) — KEEP; **5 genuinely dead**: `checklist, comparison, timeline, table, activity` (0 producers). ⚠️ `timeline`/`table` are dead **KINDS** but LIVE **component types** — prune the kinds only.
- **Dead-kind detail builders** in `src/services/surface_detail_builders/lists.py` (all 10 fns for the 5 dead kinds), imported + registered in `__init__.py` (imports `:18-29`, `TAB_BUILDERS` `:76-85`, `__all__` `:111-119`).
- **Narrative flattened to `Text`/`Caption`** (no markdown ComponentType on backend): `briefing.py:43` (`why`→`caption`), `:128` (`desc`→`caption`); `insight.py:35` (`signal_summary`→`text`), `:43` (`relevance_reasoning`→`caption`). **`react-markdown` already a FE dep** (`package.json:19-20`); FE already renders markdown via `frontend/src/components/jarvis/markdown-renderer.tsx` (`MarkdownRenderer`, `InlineMarkdown`).
- **Three approval representations**: (1) WS `SurfaceUpdate.approval: ApprovalContext` → `InlineApprovalCard` (`inline-approval.tsx`), actions via WS `sendAction({id})` — **part of the phase machine, DO NOT TOUCH**; (2) REST `approval_card()` button tree (`src/ui/units.py:184`), actions via `routeApprovalAction` (`approval-actions.ts:45`) → REST `POST /v1/approvals/{id}/…`; (3) legacy `approval`-kind badge shim (`surface-card.tsx:27,46`), non-interactive. Bridged in `surface-detail-modal.tsx:41-57`. `approvalToSurface` confirmed deleted. `approval.edit` (`approval-actions.ts:74-88`) is an informative no-op.
- **Eviction of `ui_surfaces` is 24h read-filter + 48h hard-delete** (`eviction_service.py:30,196`), NOT the "90-day" in CLAUDE.md/memory.
- **`JARVIS_RUNTIME` default `legacy`** (`settings.py:172`); deep sub-flags all `False`. Deep path dormant; nothing in Step 9 is gated behind it.

---

## File structure (what each phase touches)

**P0 — characterization guardrails (tests only):**
- Create: `backend/tests/test_step9_surface_characterization.py`, `frontend/src/components/a2ui/renderer.characterization.test.tsx`

**P1 — prune dead schema (backend + frontend, LIVE):**
- Modify: `backend/src/ui/contracts.py` (drop 13 enum members + 5 kinds + docstring), `backend/src/ui/renderer.py` (drop 13 builders), `backend/src/ui/component_properties.py` (drop dead-type property models), `backend/src/services/surface_detail_builders/__init__.py` (drop dead-kind imports/registry/`__all__`)
- Delete: `backend/src/services/surface_detail_builders/lists.py`
- Modify: `frontend/src/components/a2ui/renderer.tsx` (drop 13 imports + 13 cases), `frontend/src/lib/a2ui-types.ts` (drop dead prop types if any)
- Delete: 13 frontend component files (`avatar, calendar, chart, column, data-grid, form, kanban-board, modal, select, status-indicator, tabs, text-field, toggle`.tsx) + any co-located `.test.tsx`

**P2 — narrative → markdown (backend + frontend, additive):**
- Modify: `backend/src/ui/contracts.py` (add `MARKDOWN` enum), `backend/src/ui/renderer.py` (add `markdown()` builder), `backend/src/ui/component_properties.py` (add `Markdown` property model), `backend/src/services/surface_detail_builders/briefing.py` + `insight.py` (rewire narrative)
- Create: `frontend/src/components/a2ui/components/markdown.tsx`
- Modify: `frontend/src/components/a2ui/renderer.tsx` (add `Markdown` case + import)

**P3 — bounded approval de-dup (backend + frontend, LIVE):**
- Modify: `backend/src/ui/units.py` (drop the Edit button from `approval_card`), `frontend/src/components/a2ui/approval-actions.ts` (drop `approval.edit`), `frontend/src/components/a2ui/components/inline-approval.tsx` (accept both transports), `frontend/src/components/workspace/surface-detail-modal.tsx` (route both reps through `InlineApprovalCard`), `frontend/src/components/workspace/surface-card.tsx` (drop legacy `approval`-kind badge shim)

**P4 — holistic + gates + docs:** no source (memory + ledger + optional CLAUDE.md).

---

## Phase 0 — Characterization guardrails

**Goal:** Pin the render behavior that P1–P3 must NOT regress, so every deletion is fenced by a green test. These PASS immediately (they snapshot current behavior); P2 intentionally updates the briefing/insight ones.

### Task 0.1: Backend characterization — live builders produce expected trees

**Files:** Create `backend/tests/test_step9_surface_characterization.py`

- [ ] **Step 1: Write characterization tests** that assert the LIVE builders emit their current component-type sets. Use `make_mock_settings()` + real builder calls with in-memory/mock data. Assert (a) `briefing.build_briefing_priorities` over a priorities list yields `Text`+`Caption`(+`Divider`) nodes; (b) `insight.build_insight_signal` yields `Badge`+`Text`+`Metric`+`Caption`; (c) `renderer.py` exposes the 16 live builders and each emits its expected `type` string; (d) a full `ComponentType` membership assertion listing the **current** 29 values (this test will be edited in P1 to the post-prune set — it is the deletion tripwire).

```python
# key assertions (illustrative — flesh out with the repo's builder-test idiom)
from src.ui import renderer as r
from src.ui.contracts import ComponentType

def test_live_component_types_present():
    live = {"Text","Badge","Row","Card","Metric","Button","Alert","List",
            "Table","Timeline","MemoryCard","Divider","CodeBlock","Progress",
            "EntityCard","ExecutionTrace"}
    values = {ct.value for ct in ComponentType}
    assert live <= values  # all live types exist

def test_component_type_count_is_29_pre_prune():
    # TRIPWIRE: P1 edits this to 16 (+1 Markdown in P2 = 17). Fails loudly if the
    # enum drifts unexpectedly.
    assert len({ct.value for ct in ComponentType}) == 29

def test_briefing_priorities_emits_text_and_caption():
    comp = r.text("t", "hello")
    assert comp.type == "Text"
    cap = r.caption("c", "why")
    assert cap.type == "Text"  # caption is a Text variant — confirm at impl time
```

- [ ] **Step 2: Run — expect PASS** (pins current behavior). `cd backend && uv run pytest tests/test_step9_surface_characterization.py -v` → PASS.
- [ ] **Step 3: Commit.** `git add backend/tests/test_step9_surface_characterization.py && git commit -m "test(rebuild): Step 9 P0 backend surface characterization guardrails"`

### Task 0.2: Frontend characterization — renderer dispatch + unknown-type fallback

**Files:** Create `frontend/src/components/a2ui/renderer.characterization.test.tsx`

- [ ] **Step 1: Write a renderer smoke test** (Vitest/RTL — match the existing `renderer.test.tsx` harness) asserting: (a) each of the 16 LIVE component types renders without hitting the `[Unknown: …]` fallback; (b) an unknown `type:"Bogus"` renders the `[Unknown: Bogus]` fallback (`renderer.tsx:164-169`); (c) a `type:"Chart"` currently renders its component (this assertion FLIPS to fallback in P1 — the deletion tripwire).
- [ ] **Step 2: Run — expect PASS.** `cd frontend && npm run test -- renderer.characterization` (or the repo's test runner) → PASS. If no unit-test runner is wired, use `npm run build` as the type-level guard and note it in the test file header.
- [ ] **Step 3: Commit.** `git add frontend/src/components/a2ui/renderer.characterization.test.tsx && git commit -m "test(rebuild): Step 9 P0 frontend renderer characterization guardrails"`

> **CHECKPOINT after P0:** full backend gate `uv run pytest tests/ --ignore=tests/e2e` (3327+/18) + `npm run lint && npm run build`. Both green.

---

## Phase 1 — Prune dead schema (13 component types + 5 surface kinds)

**Single-owner-per-file + SYNCHRONOUS dispatch** for the hot files (`contracts.py`, `renderer.py`, `renderer.tsx`, `__init__.py`). Sequence backend before frontend so the backend contract shrinks first.

### Task 1.1: Prove the 13 component types + 5 kinds are dead (safety gate)

**Files:** none (verification)

- [ ] **Step 1: Census the 13 component builders** — each must have 0 non-test, non-`renderer.py`-def call-sites:
```bash
cd backend
for fn in chart calendar_view kanban_board data_grid toggle select_field text_field avatar status_indicator modal tabs form column; do
  echo "$fn: $(grep -rn "\.${fn}(" src --include='*.py' | grep -v 'src/ui/renderer.py' | grep -vi test | wc -l | tr -d ' ')"
done   # ALL must print 0
```
- [ ] **Step 2: Census the 5 dead kinds** — `checklist comparison activity` (and the KIND uses of `timeline`/`table`) must have 0 producers outside `contracts.py`/`surface_detail_builders`/tests. Confirm `derive_surface_kind` (`surface_mapping.py`) emits NONE of the 5 (it emits only `briefing`/`alert`/`plan`/`summary`).
- [ ] **Step 3: Grep frontend importers** of the 13 dead component files OUTSIDE `renderer.tsx` — each must be import-only from renderer:
```bash
cd frontend
for c in avatar calendar chart column data-grid form kanban-board modal select status-indicator tabs text-field toggle; do
  echo "$c: $(grep -rln "components/${c}\"" src | grep -v 'renderer.tsx' | wc -l | tr -d ' ')"
done   # ALL must print 0 (only renderer.tsx imports them)
```
- [ ] **Step 4:** If any count is non-zero, STOP and re-scope (that type/kind is not dead). Record the census output in the commit message.

### Task 1.2: Backend — drop dead component types (enum + builders + property models + docstring)

**Files:** Modify `backend/src/ui/contracts.py`, `backend/src/ui/renderer.py`, `backend/src/ui/component_properties.py`

- [ ] **Step 1: Update the P0 tripwire test FIRST** — change `test_component_type_count_is_29_pre_prune` to assert `== 16` (rename to `_post_prune`), and update `test_live_component_types_present` to assert equality (`== live`, not `<=`). Run → FAIL (enum still 29). This is the RED that drives the deletion.
- [ ] **Step 2: Delete the 13 enum members** from `ComponentType` (`contracts.py:82 COLUMN, :84 TABS, :85 MODAL, :94 DATA_GRID, :98 CHART, :101 TEXT_FIELD, :102 SELECT, :103 TOGGLE, :104 FORM, :107 AVATAR, :108 STATUS_INDICATOR, :113 KANBAN_BOARD, :114 CALENDAR`). Keep `ROW, CARD, DIVIDER, TEXT, CODE_BLOCK, BADGE, ALERT, TABLE, TIMELINE, METRIC, PROGRESS, BUTTON, LIST, ENTITY_CARD, MEMORY_CARD, EXECUTION_TRACE` (16). Update the module docstring (`:6-12`) to the surviving set.
- [ ] **Step 3: Delete the 13 builder functions** from `renderer.py` (`chart, calendar_view, kanban_board, data_grid, toggle, select_field, text_field, avatar, status_indicator, modal, tabs, form, column`).
- [ ] **Step 4: Delete their `PROPERTY_MODELS` entries** in `component_properties.py` (grep the 13 type strings; remove the model classes + registry rows).
- [ ] **Step 5: Run backend gate** — `uv run pytest tests/ --ignore=tests/e2e -q`. Expect the P0 tripwire GREEN + any test that referenced a dead builder/type to FAIL → delete/adjust those tests (they tested dead code). Re-run to green. `ruff check src tests --fix`.
- [ ] **Step 6: Commit.** `git commit -am "refactor(rebuild): Step 9 P1 drop 13 never-produced A2UI component types (backend)"`

### Task 1.3: Backend — drop 5 dead surface kinds (Literal + lists.py + registry)

**Files:** Modify `backend/src/ui/contracts.py`, `backend/src/services/surface_detail_builders/__init__.py`; Delete `backend/src/services/surface_detail_builders/lists.py`

- [ ] **Step 1: Delete the 5 kinds** from `SurfaceKind` (`contracts.py:43 checklist, :45 comparison, :46 timeline, :47 table, :48 activity`). KEEP `plan` (`:42`) and `approval` (`:44`). Leave `SYSTEM_SURFACE_KINDS`/`AGENT_SURFACE_KINDS` unchanged (they don't list these).
- [ ] **Step 2: Delete `lists.py`** entirely (all 10 builders for the 5 dead kinds).
- [ ] **Step 3: In `__init__.py`** remove: the `.lists` import block (`:18-29`), the 10 `TAB_BUILDERS` rows (`:76-85`), the 10 `__all__` entries (`:111-119`).
- [ ] **Step 4: Run backend gate.** Delete/adjust any test that built a `checklist/comparison/timeline/table/activity` surface or imported a `lists.py` builder. Green + ruff.
- [ ] **Step 5: Commit.** `git commit -am "refactor(rebuild): Step 9 P1 drop 5 dead surface kinds + lists.py detail builders"`

### Task 1.4: Frontend — drop 13 dead components (imports + cases + files + types)

**Files:** Modify `frontend/src/components/a2ui/renderer.tsx`, `frontend/src/lib/a2ui-types.ts`; Delete 13 component files (+ co-located tests)

- [ ] **Step 1: Update the P0 frontend tripwire** — flip the `Chart` assertion to expect the `[Unknown: Chart]` fallback; update the live-types list to 16. Run → FAIL (Chart still renders). RED.
- [ ] **Step 2: Delete the 13 imports** (`renderer.tsx:6,9,11,13,14,18,19,23,26,27,29,31,33`) and the 13 switch cases (`Column:102-103, Tabs:108-117, Modal:118-119, Form:120-121, TextField:126-127, Select:128-129, Toggle:130-131, DataGrid:136-137, Chart:144-145, Avatar:148-149, StatusIndicator:150-151, KanbanBoard:160-161, Calendar:162-163`). Keep the `default` fallback and the 16 live cases.
- [ ] **Step 3: Delete the 13 component files** (`avatar, calendar, chart, column, data-grid, form, kanban-board, modal, select, status-indicator, tabs, text-field, toggle`.tsx) and any co-located `.test.tsx`.
- [ ] **Step 4: Clean `a2ui-types.ts`** — remove prop interfaces exclusively used by the deleted components (grep each type name; remove only if 0 remaining refs).
- [ ] **Step 5: Frontend gate** — `npm run lint && npm run build`. Fix any dangling import. Expect the P0 tripwire GREEN.
- [ ] **Step 6: Commit.** `git commit -am "refactor(rebuild): Step 9 P1 drop 13 dead A2UI components (frontend)"`

> **CHECKPOINT after P1:** full backend gate (green, 18 skipped) + `npm run lint && build` (green). **Review: 2-stage PARALLEL spec+quality on the frozen P1 commit range** (shared-seam deletion, both stacks). Negative control WITH TEETH: reviewer independently re-runs Task-1.1 census + reverts one enum deletion → tripwire test must FAIL → restore → green.

---

## Phase 2 — Narrative → markdown

### Task 2.1: Backend — add `Markdown` component type + builder + property model

**Files:** Modify `backend/src/ui/contracts.py`, `backend/src/ui/renderer.py`, `backend/src/ui/component_properties.py`; Test in `backend/tests/test_step9_surface_characterization.py`

- [ ] **Step 1: Write failing test** — assert `renderer.markdown("id", "# H\n- a\n- b").type == "Markdown"` and `.properties["content"] == "# H\n- a\n- b"`; assert `"Markdown"` ∈ `ComponentType`. Run → FAIL.
- [ ] **Step 2: Add `MARKDOWN = "Markdown"`** to `ComponentType` (Text group) + update docstring + the P0 count tripwire to `== 17`.
- [ ] **Step 3: Add the builder** to `renderer.py`:
```python
def markdown(component_id: str, content: str) -> A2UIComponent:
    """Render GitHub-flavored markdown as a single block. Preserves paragraph/
    list/emphasis structure the frontend renders via react-markdown."""
    return A2UIComponent(type="Markdown", id=component_id, properties={"content": content})
```
- [ ] **Step 4: Add a property model** to `component_properties.py` `PROPERTY_MODELS` for `"Markdown"` → `{"content": str}` (mirror the existing `Text`/`Caption` property-model idiom).
- [ ] **Step 5: Run** the new test + full backend gate → PASS. ruff.
- [ ] **Step 6: Commit.** `git commit -am "feat(rebuild): Step 9 P2 add Markdown A2UI component type + builder"`

### Task 2.2: Frontend — `Markdown` component + renderer case

**Files:** Create `frontend/src/components/a2ui/components/markdown.tsx`; Modify `frontend/src/components/a2ui/renderer.tsx` (+ `a2ui-types.ts` prop type)

- [ ] **Step 1:** Create `markdown.tsx` rendering `component.properties.content` via the existing `MarkdownRenderer` (`@/components/jarvis/markdown-renderer`):
```tsx
"use client";
import type { A2UIComponent } from "@/lib/a2ui-types";
import { MarkdownRenderer } from "@/components/jarvis/markdown-renderer";

export function A2UIMarkdown({ component }: { component: A2UIComponent }) {
  const content = typeof component.properties?.content === "string" ? component.properties.content : "";
  return <MarkdownRenderer>{content}</MarkdownRenderer>;
}
```
- [ ] **Step 2:** Add the import + `case "Markdown": return <A2UIMarkdown key={component.id} component={component} />;` to `renderer.tsx` (Text group). Add the `content` prop type to `a2ui-types.ts` if props are typed.
- [ ] **Step 3:** Frontend gate `npm run lint && npm run build` → green. Add/extend the characterization test: a `Markdown` component renders its content (not the `[Unknown]` fallback).
- [ ] **Step 4: Commit.** `git commit -am "feat(rebuild): Step 9 P2 frontend Markdown A2UI component (react-markdown)"`

### Task 2.3: Rewire briefing + insight narrative to markdown

**Files:** Modify `backend/src/services/surface_detail_builders/briefing.py`, `insight.py`; update the P0 characterization test (intended behavior change)

- [ ] **Step 1: Update the P0 characterization assertions** for briefing/insight to expect `Markdown` where narrative prose was flattened. Run → FAIL (still `Caption`/`Text`). RED.
- [ ] **Step 2: Rewire the 4 narrative sites** to `r.markdown(...)` (keep short labels/titles as `Text`; only prose bodies become markdown):
  - `briefing.py:43` `r.caption(f"pri_{i}_why", why)` → `r.markdown(f"pri_{i}_why", why)`
  - `briefing.py:128` `r.caption(f"act_{i}_desc", desc)` → `r.markdown(f"act_{i}_desc", desc)`
  - `insight.py:35` `r.text("ins_summary", signal_summary)` → `r.markdown("ins_summary", signal_summary)`
  - `insight.py:43` `r.caption("ins_reasoning", relevance_reasoning)` → `r.markdown("ins_reasoning", relevance_reasoning)`
- [ ] **Step 3: Run** full backend gate → PASS. ruff.
- [ ] **Step 4: Commit.** `git commit -am "feat(rebuild): Step 9 P2 route briefing/insight narrative through Markdown component"`

> **CHECKPOINT after P2:** full backend gate + frontend gate green. **Review: combined per-phase** (additive, low blast radius) + a manual render check that a multi-line/bulleted `signal_summary` renders structured (not one flat line).

---

## Phase 3 — Bounded approval de-dup (frontend consolidation + dead-shim removal)

**Scope guard (from Fork 1):** touch ONLY the live, non-phase-machine approval surface. **DO NOT** modify `SurfaceUpdate.approval` / `ApprovalContext` (`contracts/__init__.py`), the phase machine emitters, or the 6B deep `approval_needed` frame (`stream_adapter.py`). The backend "one interrupt event" contract unification is **deferred to Step 10** (ledger). This phase = (a) retire the dead `approval.edit` no-op, (b) drop the legacy `approval`-kind badge shim, (c) render both live reps (WS `ApprovalContext` + REST `approval_card`) through the SINGLE `InlineApprovalCard` component.

### Task 3.1: Retire the dead `approval.edit` no-op (backend button + frontend handler)

**Files:** Modify `backend/src/ui/units.py`, `frontend/src/components/a2ui/approval-actions.ts`

- [ ] **Step 1: Write/adjust a test** asserting `units.approval_card(...)` emits only Approve + Reject buttons (no `approval.edit`). Run → FAIL (Edit present).
- [ ] **Step 2: Remove the Edit button** from `approval_card()` (`units.py:~255` the `approval.edit` button) so no orphan button is emitted.
- [ ] **Step 3: Remove `"approval.edit"`** from `APPROVAL_ACTION_TYPES` (`approval-actions.ts:15`) + the `case "approval.edit"` block (`:74-88`) + the now-unused `editApproval`/`isEditBody`/`EditApprovalBody` (`:12,79,99-113`) — grep each for other callers first; remove only if unused.
- [ ] **Step 4:** Backend gate + `npm run lint && build` → green.
- [ ] **Step 5: Commit.** `git commit -am "refactor(rebuild): Step 9 P3 retire dead approval.edit no-op (both stacks)"`

### Task 3.2: Render both live approval reps through one `InlineApprovalCard`

**Files:** Modify `frontend/src/components/workspace/surface-detail-modal.tsx`, `frontend/src/components/a2ui/components/inline-approval.tsx`, `frontend/src/components/a2ui/approval-actions.ts`

- [ ] **Step 1: Read** `inline-approval.tsx` (the WS-driven card), `surface-detail-modal.tsx:41-57` (the try-REST-then-WS bridge), and `units.approval_card()`. Confirm the data both reps carry (risk, trust_level, decisions, approval_id).
- [ ] **Step 2: Write a component test** (RTL) asserting `InlineApprovalCard` renders + dispatches for BOTH inputs: (a) a WS `ApprovalContext` (approve/reject via WS `sendAction({id})`); (b) a REST-shaped approval (approve/reject via `routeApprovalAction` → REST). Run → FAIL (REST rep still goes through the generic button tree).
- [ ] **Step 3: Extend `InlineApprovalCard`** to accept an optional `transport: "ws" | "rest"` (default `"ws"`, preserving current behavior) and, when `"rest"`, dispatch approve/reject via `routeApprovalAction`. In `surface-detail-modal.tsx`, render the REST/`approval`-detail-tab approval through `InlineApprovalCard transport="rest"` instead of the raw `approval_card` button tree, and keep the WS live-surface path on `transport="ws"`. Simplify the `handleAction` bridge accordingly (one component, explicit transport — not a try/fall-through).
- [ ] **Step 4:** `npm run lint && build` + component test → green.
- [ ] **Step 5: Commit.** `git commit -am "refactor(rebuild): Step 9 P3 one InlineApprovalCard for both live approval transports"`

### Task 3.3: Drop the legacy `approval`-kind badge shim

**Files:** Modify `frontend/src/components/workspace/surface-card.tsx`

- [ ] **Step 1:** Confirm no standalone `approval`-kind surface is produced (backend `derive_surface_kind` never returns it; `SurfaceService` folds approvals into the `run` surface). Old persisted `approval`-kind rows age out via the 48h TTL.
- [ ] **Step 2:** Remove the legacy `approval` entries from the `kindLabel`/`kindColor` "Legacy" maps (`surface-card.tsx:27,46`) and any `approval`-kind-specific badge branch. Unknown kinds already fall back to the generic card, so any lingering old row still renders safely until TTL.
- [ ] **Step 3:** `npm run lint && build` → green.
- [ ] **Step 4: Commit.** `git commit -am "refactor(rebuild): Step 9 P3 drop legacy approval-kind badge shim"`

> **CHECKPOINT after P3:** full backend gate + frontend gate green. **Review: independent opus** (highest-risk phase — live approval UX). Negative controls WITH TEETH: (a) a test proving `SurfaceUpdate.approval` / the WS live path is UNCHANGED (byte-diff the phase-machine files = no change); (b) revert Task-3.2's transport branch → the both-transports test FAILS → restore.

---

## Phase 4 — Holistic review + gates + docs

### Task 4.1: Final holistic (opus) + full gates both stacks

- [ ] **Step 1:** Independent opus holistic over the full P1→P3 diff. It MUST independently reproduce every negative control RED→restore→GREEN: P1 enum-deletion tripwire, P2 markdown-rewire characterization, P3 both-transports test + phase-machine-unchanged guard.
- [ ] **Step 2:** Full backend gate `uv run pytest tests/ --ignore=tests/e2e` (expect **18 skipped**, NOT ~108; +N tests) + `alembic heads` (still `1a2770a28c39`, NO migration) + `alembic check` drift-free + `ruff check src tests`. Frontend `npm run lint && npm run build`.
- [ ] **Step 3:** Confirm the phase machine is untouched: `git diff 1f343cc..HEAD -- backend/src/contracts/__init__.py backend/src/services/execution_surface_emitter.py backend/src/services/graph_executor.py backend/src/services/dag_runner.py backend/src/services/trust_gate.py backend/src/deep_runtime/stream_adapter.py` → **empty**.

### Task 4.2: Docs — memory + ledger (+ optional CLAUDE.md)

- [ ] **Step 1:** Append a "STEP 9 DONE = SHIP" block to `project_first_principles_rebuild.md` + update MEMORY.md pointer.
- [ ] **Step 2:** Update the activation-gate ledger: mark Step 9 done; ADD the deferred items (see below) to Category B/C.
- [ ] **Step 3 (decide per doc policy):** CLAUDE.md's "A2UI System" section has stale facts this step corrects (surface-kind list includes removed `execution`/misses `run`; "SurfaceUpdate in `src/ui/contracts.py`"; "90-day retention" for surfaces). Since Step 9 is a LIVE runtime-agnostic change to the durable UI layer, a **small durable correction** to that section IS warranted (unlike the dormant deep-internal steps). Scope it to: the corrected surface-kind taxonomy, the new `Markdown` component, the 24h/48h surface TTL, and the `SurfaceUpdate` location — NO step-migration narrative.
- [ ] **Step 4: Commit.** `git commit -m "docs(rebuild): Step 9 EXECUTED=SHIP — plan outcome + ledger + A2UI section correction"`

---

## Review strategy (summary)

- **P0**: tests pass on write (guardrails). **P1** (shared-seam deletion, both stacks) = **2-stage PARALLEL spec+quality on the frozen commit range**; reviewer re-runs the census + a teeth negative control. **P2** (additive) = combined per-phase. **P3** (highest-risk, live approval UX) = **independent opus** + phase-machine-unchanged byte-diff guard. **P4** = independent opus holistic reproducing every negative control RED→restore→GREEN.
- **Single-owner-per-file + SYNCHRONOUS implementer dispatch** for `contracts.py`, `renderer.py`, `renderer.tsx`, `__init__.py`, `component_properties.py`, `a2ui-types.ts` (touched across P1/P2/P3 — sequence their edits).
- **FULL gate at EVERY checkpoint** (backend 18-skipped-not-~108 + frontend lint+build). Wire-then-full-gate: after any deletion, run the whole suite — deletions surface latent references (Step-8 P3 lesson).
- **Negative controls WITH TEETH** — a guard a one-line revert doesn't break is not a guard (Step-8 P2 lesson): the enum-count tripwire, the both-transports approval test, the phase-machine byte-diff.
- **Verify-don't-trust** every current-state claim at file:line, including extraction agents AND reviewers (Step-8: an extraction agent and a reviewer each carried a false claim). Settle disagreements by reading installed source.

## Deferred → ledger (added at plan-close)

- **B (Category B, Step-10 cutover):** native-stream→`surface_update` translation adapter — build when the autonomous path runs on the deep runtime (no source phases to translate until then). The full "one interrupt approval event" backend contract unification (spanning WS `ApprovalContext` + deep `approval_needed` frame) — converge when the phase machine is reworked post-Step-10.
- **C (Category C, opportunistic):** the mechanical A2UI→SurfaceKit rename (with the standards track); the AG-UI transport / MCP-Apps artifact adoption (separate `project_week3_standards_adoption` track); phase-machine deletion (spec-explicit, after Step 10); the two dead phase arms `planning`/`partial` (drop with the phase-machine rework).

## Self-review (writing-plans checklist — done)

- **Spec coverage:** §4.9 bullets each mapped (adapter→deferred; phase-machine→untouched; version→TTL-prune; prune→P1; narrative→P2; approval→P3 bounded; rename→deferred). Gaps: none in Step-9 scope; deferrals recorded in the ledger section.
- **Placeholder scan:** deletions specify exact symbols/lines + a census gate; additive/rewire steps carry real code; no "add error handling"/"TBD".
- **Type consistency:** `markdown(component_id, content)` / `A2UIMarkdown` / `"Markdown"` used consistently across 2.1–2.3; `ComponentType` count tripwire threaded 29→16→17 across P0/P1/P2; `transport:"ws"|"rest"` consistent across 3.2.
