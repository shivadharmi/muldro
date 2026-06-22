# Design Sync — Live frontend ↔ imported `jarvis_design` UI kit

**Goal:** Align the live `frontend/src/` with the idealized design in `.claude/skills/jarvis_design/`,
including the backend data the design needs. Full alignment (all tracks). Settings → restore the
documented 5-tab IA, rendered as a Claude-style **popup/modal with sidebar tabs** opened from the nav.

**Branch:** `review/architecture-remediation` (in-place; design assets are untracked so no worktree).

**Design reference:** `.claude/skills/jarvis_design/README.md` (rules), `colors_and_type.css` (tokens),
`ui_kits/web_app/components/*.jsx` (idealized target), `ui_kits/web_app/components/data.js` (data contract).

**Invariants (do not break):**
- A2UI surfaces must never have empty `children[]`; build via `renderer.py` builders server-side.
- Use `SurfaceService`; no client-side surface conversion.
- Frontend: hooks before conditional returns; no side effects in render; `useSurfaceStore` (not `useSurfaceState`).
- Backend: `async with db_factory()`; Pydantic response models; `/v1/` routes; workspace-scoped.
- No new volatile counts in docs. Conventional commits, no Co-Authored-By.
- Tokens only — no hardcoded hex in frontend; use `surface-*/t-*/j-*` + design-token helpers.

---

## WAVE 1 — Backend data contract & endpoints (must land before frontend rendering)

### B1 — Surface contract extension + builders + alert kind  [foundational]
Extend the surface preview/live contract so per-kind design fields have a home, then populate them.
- `backend/src/ui/contracts.py`: add to `SurfacePreview`: `tokens:int|None`, `cost_usd:float|None`,
  `risk:str|None` (low/medium/high/critical), `flags:list[str]` (e.g. ["Irreversible","LEARNING"]),
  `items:list[str]` (briefing priorities), `evidence:str|None` ("42 days observed"), `updated_at:datetime|None`.
  Add `alert` to `SurfaceKind`.
- `backend/src/contracts/__init__.py`: mirror `tokens`/`cost_usd` onto `SurfaceUpdate` (live frames) and
  `WorkspaceSurfacePush` where the card needs them.
- `backend/src/services/surface_builder.py`: populate the new fields per kind:
  - run/execution → tokens, cost_usd (from `TaskRun.input/output_tokens`, `cost_usd`), updated_at
  - briefing → items (from `briefing.top_priorities`)
  - approval context → risk, flags (reversible→"Irreversible", trust level→"LEARNING"/etc.)
  - **emit `kind="alert"` for failed runs** instead of folding into `recommendation`
    (the alert detail builders in `surface_detail_builders/` already exist).
- `backend/src/services/surface_pusher.py` (or jarvis push paths): carry tokens/cost on live frames.
- Tests: extend surface_builder tests to assert new fields populate; assert alert kind for a failed run.

### B2 — History list fields  [independent of B1]
- `backend/src/api/routes_history.py` + `schemas_history.py`: populate `cost_usd` (currently always null;
  derive from trace rollup / `TaskRun.cost_usd`), add `agent` (primary agent from trace),
  add `duration_ms` (or expose `started_at`/`completed_at` consistently for FE to derive — prefer a
  computed `duration_ms`), ensure `updated_at`.
- Tests: assert history items carry cost/agent/duration.

### B3 — Search source exposure  [independent]
- `backend/src/api/schemas.py` `SearchResult`: add `source_db:str|None` and `why_matched:str|None`
  (route already computes them; `extra="ignore"` drops them). Remove the strip or add explicit fields.
- `backend/src/api/routes_search.py`: ensure populated.
- Tests: assert source_db present in search response.

### B4 — Insight evidence persistence  [independent]
- `backend/src/contracts/__init__.py` `InsightSurfaceData`: add `evidence:str|None` (or `evidence_count:int`).
- Wire planner `evidence_count` (`prompts.py` emits it) → `InsightSurfaceData` → `push_insight_surface`
  (`surface_pusher.py`) and onto the insight preview.
- Tests: assert evidence flows to the insight surface.

### B5 — Live active agents projection  [independent]
- Add a "currently executing agents" projection. Inspect `backend/src/api/routes_runtime.py` and
  `runtime_projection.py`. Expose `active_agents: list[str]` (distinct agent names of in-flight steps/runs)
  on `/v1/runtime/summary` (or `/v1/system/dashboard`).
- Tests: assert active_agents reflects running steps.

### B6 — Integrations logo + scope coarsening  [independent]
- `backend/src/services/integration_status.py` / `routes_integrations.py`: add `slug` (stable key) and
  coarsen capability scopes to `read`/`write` for the design pill (keep raw scopes too). `logo` can stay
  frontend-mapped by slug, but expose the slug reliably.
- Tests: assert slug + coarsened scopes present.

### B7 — Memory cards feed (kind/label/sources)  [large rethink, independent]
Design memory card = `{kind: person|project|fact|preference, label, desc, sources[]}`.
Backend memory taxonomy is orthogonal; person/project come from the **entity graph**.
- Decide feed: surface a unified "knowledge cards" projection that maps:
  - entities (person/project/org) → person/project kinds with `label`=name, `sources`=source systems
  - memories (preference/semantic) → fact/preference kinds with `label`/`desc`=fact_text
- Implement an endpoint (extend `/v1/knowledge/...`) returning the design card shape with `sources[]`
  resolved to source-system slugs (from `source_event_ids` → event source).
- Tests: assert card shape with kind/label/sources.

---

## WAVE 2 — Frontend types & rendering (depends on Wave 1)

### F1 — lib types sync  [foundational for FE]
- `frontend/src/lib/a2ui-types.ts` + `frontend/src/lib/types/*`: add the new SurfacePreview fields,
  history fields, search source, insight evidence, integrations slug/scopes, knowledge card shape.

### F2 — SurfaceCard anatomy  (`components/workspace/surface-card.tsx`)
- Replace bare status dots with `StatusBadge` pills (dot + label) across all kinds.
- Approval card: render `risk` chip ("HIGH RISK", risk-colored) + `flags` chips ("Irreversible","LEARNING").
- Briefing card: render `items[]` as a bullet list.
- Execution card: render `tokens` (mono) + `cost` (success-green, `$0.000`, tabular-nums).
- Insight card: violet "Insight" badge + "Proposal" pill + `evidence` micro-line.
- alert kind: red styling already mapped; ensure "Failed" pill.

### F3 — Workspace System Status Bar  (`components/workspace/`, `app/page.tsx`)
- Build the full-width horizontal 4-segment bar (Health · Daily Budget w/ bar · Queue · Agents) under the
  hero, matching `Workspace.jsx` `WorkspaceStatusBar`. Render `activeAgents` as chips. Keep mono/tabular nums.

### F4 — History token port + new fields  (`app/history/*`, `components/history/*`)
- Replace ~56 hardcoded hex with tokens (`surface-*/t-*/j-*`); reuse system `StatusBadge`.
- Render cost (`$0.000`), agent, duration on rows. (`run-detail-modal.tsx` is the reference — already clean.)

### F5 — Search source unify  (`app/search/*`, `components/feature/search/*`)
- One source vocabulary everywhere (friendly: Vector/Keyword/Graph) via `sourceDbStyle()`; show source badges
  on list rows too. Delete dead `components/search/search-results.tsx`.

### F6 — Chat fidelity  (`components/jarvis/*`, `a2ui/components/*`)
- Add message-level token/cost/duration footer (aggregate per-agent steps), mono + tabular-nums.
- Fix running glyph in `agent-trace.tsx` (`○`→ pulsing `◉`); lowercase agent names; `font-mono`+`tabular-nums`
  on metric spans; `pipeline · {agent}` eyebrow + StatusBadge.
- StepList: add `sub` subtitle line + mm:ss timer format.
- Composer: multiline textarea (shift+enter), agent/context/mode chips, mic + send-icon, task-framed placeholder.
- Bring execution `StepList` inline into the chat thread (not only the surfaces pane).

### F7 — Chrome  (`components/shell/*`, `components/layout/*`)
- TopBar: pulsing "live" dot + mono running-tool + queue-count badge.
- Sidebar: restore user tile (avatar/name/email) + monogram SVG; keep theme toggle accessible; active nav dot.
- CommandLauncher: empty-state Suggestions rows. Ellipsis `…` fixes.
- ContextSidebar: entities as chips + recency/confidence/sources rollup.

### F8 — Settings 5-tab popup  (`app/settings/*` → modal; `components/layout/sidebar.tsx`)
- Restore 5 tabs: Account, Preferences, Policy, Budget, Trust — as a **Claude-style popup/modal with a
  left tab rail** opened from the sidebar (not a full page). Split the merged "How Jarvis acts" back into
  Policy + Trust; rename "Spending" segment to Budget; create the missing Preferences tab.

### F9 — Insight icons + Knowledge source chips
- `a2ui/components/insight-surface.tsx`: emoji source icons → inline stroke SVG.
- `components/knowledge/memory-row.tsx`: render per-card `sources[]` provenance chips (from B7).

---

## WAVE 3 — Review & verify
- `cd backend && ruff check src/ tests/ && pytest tests/ -q` green.
- `cd frontend && npm run lint && npm run build` green.
- code-reviewer subagents on backend + frontend diffs; fix CRITICAL/HIGH.
- Update MEMORY/docs only for durable IA change (Settings popup IA).
