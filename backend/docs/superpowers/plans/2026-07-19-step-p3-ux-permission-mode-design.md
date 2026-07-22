# P3 — todos surface + retire `mode` → `permission_mode` + per-workspace default — DESIGN

> Chat Permission Model, Phase 3 (the last phase before R1 + the Step-10 cutover).
> Spec: `docs/superpowers/specs/2026-07-13-chat-permission-model.md` §6. Follows the P2.5 design
> pass (`backend/docs/superpowers/plans/2026-07-18-step-p2.5-planless-design.md`). On
> `rebuild/first-principles`. **STATUS: BUILT — all 12 tasks shipped 2026-07-19 (P3a `1394fe3`..`acbf46c`,
> P3b `d5b9942`/`2b7ac46`, P3c `d08810a`..`097b20b`), plan `e250beb`. Final gate: backend 3688/18 green,
> ruff clean, single alembic head `1a2770a28c39` (ZERO migrations), frontend 124 tests + build. NOT
> pushed/merged. Per-phase security+quality reviews + a final holistic review all SHIP. See §8 for the
> build record.** Baseline at design time: HEAD `03bd913`, single alembic head `1a2770a28c39`, non-e2e
> suite 3669/18 green, ruff clean.

## 0. What settled the shape (grounded current-state, verified by name at design time)

Three read-only scouts mapped P3a/P3b/P3c; every load-bearing claim was re-read against real code
(anchors as of the design pass; **re-verify by symbol name at build time**):

1. **`write_todos` todos already stream to the frontend.** `write_todos` is a deepagents builtin
   (`DEEPAGENTS_BUILTIN_NAMES`, `src/deep_runtime/builtins.py`); Jarvis has **no reader** of the
   deepagents internal `todos` LangGraph state channel. But the todos array already flows through
   `src/deep_runtime/stream_adapter.py` as the `tool_call` frame's `input.todos` (and a matching
   `tool_result`). `frontend/src/components/jarvis/chat-panel.tsx` already handles `tool_call` /
   `tool_result` and renders a generic chip. ⟹ surfacing the todos is a **frontend interpretation of
   a frame that already exists**, not new data plumbing.

2. **`mode` and `permission_mode` are orthogonal axes on two different execution paths.**
   - Legacy `mode` (`ask`/`plan`/`execute`) is a **planning-behavior** axis on the **legacy per-step
     path** (LIVE in prod). It drives three branches in `src/orchestrator/chat_processor.py`:
     `:472` (`plan`/`execute` force the Planner vs the fast path), `:540` (`plan` sets
     `requires_user_input`), `:649` (`plan` skips risky execution + presents the plan — the
     "plan-preview" feature). Request field default `mode: str = "ask"` (`routes_chat.py:47`).
   - `permission_mode` (`auto`/`ask`/`bypass`) is a **write-gating** axis on the **deep single-lead
     path**, which is DORMANT (`deep_single_lead` off, `runtime=legacy` in prod). It only takes
     effect inside `_resolve_effective_mode` (`chat_processor.py:344`), which returns `None`
     (→ legacy) whenever deep is off. Independent field, `Literal["auto","ask","bypass"] = "auto"`
     (`routes_chat.py:53`), never derived from `mode`.
   - **The live chat path is "ungated by design"** (CLAUDE.md invariant: the user's message IS the
     authorization). `permission_mode`'s gating exists ONLY on the dormant deep path; making the
     legacy path honor it would mean **adding a synchronous write gate to legacy chat**, which breaks
     that invariant. So a `permission_mode` picker is **inert on the live path until the Step-10
     cutover** lights up the deep path.

3. **A per-workspace default needs NO migration.** `Workspace.settings` is an existing JSONB column
   (`src/models/users.py:42`). `src/services/workspace_entitlements.py` already hangs a scalar
   (`allow_bypass`) there with a fail-safe reader — the exact precedent. `_resolve_effective_mode`
   already does a workspace-scoped read (`workspace_allows_bypass`, `chat_processor.py:371`).

4. **Frontend has zero `permission_mode` awareness today** (P1 was backend-only). The chat picker
   (`stores/command-store.ts` `CommandMode`, pickers in `app/chat/page.tsx`,
   `command-composer.tsx`, `command-launcher.tsx`; body built in `lib/api.ts` `streamChat`; sent from
   `chat-panel.tsx`) drives the legacy `mode` only.

## 1. Goal & non-goals

**Goal.** Deliver the P3 UX layer of the chat permission model: (a) surface the lead's `write_todos`
as a Claude-Code-style inline checklist; (b) move the user-facing chat control from the legacy
`mode` (ask/plan/execute) axis to the `permission_mode` (auto/ask/bypass) axis across the API +
frontend; (c) add a per-workspace `permission_mode` default. This makes the UX speak the new model so
the Step-10 cutover (which lights up the deep path) is a clean flip.

**Non-goals.** The autonomous path (Planner → GraphExecutor → TrustEngine) is UNTOUCHED. The legacy
per-step chat path stays **ungated by design** — P3 does NOT add a gate to it. The actual deletion of
the internal `mode` param + its `:472/:540/:649` branches, and the CLAUDE.md two-paths invariant
rewrite (R1), stay at the **Step-10 cutover / merge**, not here. No deep-path activation.

## 2. Locked decisions (from the brainstorm, 2026-07-19)

- **D1 — P3a renders inline, frontend-only, no backend event.** The todos already arrive in the
  `write_todos` `tool_call`/`tool_result` frames; the frontend intercepts `tool == "write_todos"`
  and renders a live-updating checklist in place of the generic chip. Ephemeral per-turn (matches
  `write_todos`' nature as the lead's tactical scratchpad). **No new `SurfaceKind`, no emitter, no
  persistence, no `ui_surfaces` row, no cross-repo `SurfaceKind` drift.** Rationale: `write_todos`
  rewrites the whole list each call — it is ephemeral turn-state, not a durable artifact; persisting
  it in `ui_surfaces` (24h TTL, built for briefings/run summaries) would be a category error.
  *(A typed `AgentTodos` CoreEvent is explicitly deferred as YAGNI — the frame already carries the
  data; add it only if a later phase needs the contract explicit.)*

- **D2 — P3b is a "cosmetic-now" swap that DROPS plan-preview (LIVE change, user-authorized).**
  The frontend + API move to `auto`/`ask`/`bypass`; the legacy `mode` picker and the plan-preview
  affordance leave the UI. The new picker is **inert on the live legacy path** (permission_mode does
  nothing until the deep path is active) — it is persisted/sent and lights up at Step-10. The legacy
  path stays ungated (invariant intact). This is the ONE P3 sub-part that changes live UX; it is
  consciously authorized as an exception to the session's dormant-only default.

- **D3 — legacy `mode` survives internally for the pinned callers.** `mode` is removed from
  `ChatRequest` only; the interactive handler passes a fixed `mode="ask"` (today's interactive
  default) so live default behavior is byte-identical. The internal `mode` param on
  `process_message*` / `_process_core` and its `:472/:540/:649` branches are **untouched**, so
  `schedule_dispatch` (`mode="execute"`) and `routes_ws` (`mode="ask"`) are byte-identical. Full
  `mode` deletion is Step-10 work.

- **D4 — P3c resolves backend-authoritative, at the interactive handler.** `ChatRequest.permission_mode`
  becomes optional (`None` sentinel); when `None`, the **interactive `routes_chat` handler**
  substitutes the per-workspace default (fallback `"auto"`). Resolved at the handler, **NOT in
  `_process_core`** — because `_process_core` is shared by the pinned callers, and resolving the
  default there could leak a workspace's `bypass` default onto scheduled/WS turns. Structural scoping
  beats relying on the `can_pause=False` guard.

- **D5 — P3c stores the default in `Workspace.settings` JSONB.** `default_permission_mode` scalar,
  mirroring `allow_bypass`. **No Alembic migration.** A typed column / dedicated table is NOT
  warranted for a single scalar (codebase precedent favors the JSONB flag).

- **D6 — the chat picker sends an explicit value, seeded from the workspace default.** The picker
  `GET`s the workspace default on load and sends the explicit chosen value; the backend `None`-fallback
  (D4) serves non-frontend/omitting clients + acts as the safety default. (Alternative — an explicit
  "inherit workspace default" resting state that omits the field — was considered and set aside for
  simplicity; revisit only if the product wants a visible inherit state.)

- **D7 — build order P3a → P3b → P3c.** P3a is independent/additive. P3c depends on P3b's
  `permission_mode` field + picker. Each phase: per-phase spec → build → full gate → security+quality
  reviews → commit, subagent-driven with context-economy (main loop owns hot-file mutation +
  verify + commit; reading/reviews delegated to read-only subagents returning compact maps).

## 3. Design — the three sub-parts

### P3a — inline `write_todos` checklist (frontend-only)

- **`frontend/src/components/jarvis/chat-panel.tsx`** — in the existing SSE `switch`, intercept the
  `tool_call` (~:409) and `tool_result` (~:429) cases when `tool === "write_todos"`: route the
  `input.todos` array to a todos renderer instead of appending a generic tool chip (and suppress the
  matching `write_todos` result chip). Each `write_todos` call replaces the current list (live update
  in place).
- **New `frontend/src/components/jarvis/chat-todos.tsx`** (small) — a checklist rendering each todo's
  content + status, reusing the visual language of `components/a2ui/components/execution-surface.tsx`'s
  `StepList` (status glyphs ○ ◉ ✓). Read-only display (matches Claude Code; not interactive/clickable).
- **Backend: zero changes.**
- **Testing:** a frontend unit/render test that a `write_todos` `tool_call` frame renders the
  checklist (not a generic chip) and that a subsequent `write_todos` frame replaces it; `npm run lint`
  + `npm run build` green. Backend suite untouched (no backend edits) — run the full gate anyway to
  confirm byte-neutrality.
- **Dormancy:** `write_todos` only fires on the deep path, so no `write_todos` frames arrive on the
  live legacy path; the new rendering code is inert-but-harmless in prod until cutover.

### P3b — retire `mode` → `permission_mode` (API + frontend; LIVE)

**Backend:**
- `src/api/routes_chat.py` — **remove `mode` from `ChatRequest`** (:47). The handler (~:433) passes a
  fixed `mode="ask"` into `process_message_events`, preserving today's interactive default. (`ChatRequest`
  also gains the optional `permission_mode` from P3c — sequenced there.)
- `src/orchestrator/chat_processor.py` / `src/orchestrator/jarvis.py` — the internal `mode` param and
  the `:472/:540/:649` branches are **UNCHANGED**. Pinned callers (`schedule_dispatch.py`
  `custom_agent_task` `mode="execute"`; `routes_ws.py` surface-action / execute-insight `mode="ask"`)
  are byte-identical.

**Frontend (the live change):**
- `stores/command-store.ts` — `CommandMode "ask"|"plan"|"execute"` → `PermissionMode
  "auto"|"ask"|"bypass"`; rename state `mode`→`permissionMode` + setter; default seeded from the
  workspace default (P3c `GET`; until P3c lands, a hardcoded `"auto"`).
- Pickers — `app/chat/page.tsx`, `components/feature/command/command-composer.tsx`,
  `components/shell/command-launcher.tsx` (incl. launcher suggestions): relabel the three options to
  auto/ask/bypass with honest labels/descriptions; remove the plan/execute options (plan-preview UI
  gone).
- `lib/api.ts` `streamChat` — send `permission_mode` in the POST body (replacing `mode`).
- `components/jarvis/chat-panel.tsx` — send `permissionMode` from the store.
- Frontend `SurfaceKind` mirror (`lib/types/surfaces.ts`) — untouched (P3a adds no kind).

**Live effect:** because `permission_mode` is inert on the legacy path, sending it has no functional
effect in prod until cutover. The only real live change is the picker's option set + the loss of
plan-preview. Users on the default `ask`/`auto` see no behavioral change.

**Testing:** backend — `ChatRequest` rejects `mode` gracefully (extra field ignored) and the handler
forwards a fixed `mode="ask"`; pinned callers still pass their explicit `mode` (regression tests pin
`schedule_dispatch`/`routes_ws` to legacy, unchanged). Frontend — the picker sends `permission_mode`;
store type migration compiles; `npm run lint` + `build` green. Full backend gate.

### P3c — per-workspace `permission_mode` default (backend-authoritative)

**Backend:**
- `src/api/routes_chat.py` — `ChatRequest.permission_mode` →
  `Optional[Literal["auto","ask","bypass"]] = None`. In the interactive handler, when `None`, resolve
  via the new helper (fallback `"auto"`) and pass the resolved value into `process_message_events`.
- **New `workspace_default_permission_mode(db_factory, workspace_id) -> str`** alongside
  `src/services/workspace_entitlements.py` (it already reads `Workspace.settings`). Reads
  `Workspace.settings["default_permission_mode"]`, validates ∈ {auto,ask,bypass}, fail-safe to
  `"auto"` on missing workspace / bad value / error.
- **New `GET` + `PUT`** for the workspace default (workspace-scoped, mirroring the `routes_trust`
  PUT pattern; `get_current_workspace_id()` dependency). `PUT` validates the value and writes
  `Workspace.settings["default_permission_mode"]` (JSONB merge, not clobber). Home: a small
  `routes_workspace_settings` module or an existing workspace route — decide at build.
- **Scoping invariant:** resolution is at the interactive handler ONLY; `_process_core` and the
  pinned callers never read the default (they pass explicit / default `permission_mode`).

**Frontend:**
- Settings modal (`frontend/src/components/settings/settings-modal.tsx`, **policy tab** —
  `policy-tab.tsx`, next to the existing policy-mode dropdown) — a "Default permission mode" dropdown;
  `GET` on open, `PUT` on change; wire via the existing settings action pattern in `lib/`.
- Chat picker seeds its initial value from the workspace default (`GET` on load) — completes D6.

**Migration:** NONE (JSONB scalar).

**Testing:** `workspace_default_permission_mode` returns the stored value / fail-safe `"auto"` on
unset/bad/missing/error; the interactive handler substitutes the default when `permission_mode` is
`None` and honors an explicit per-turn value when present; **pinned callers never receive a
workspace-default-derived `bypass`** (regression pin); `PUT` validates + JSONB-merges without
clobbering sibling keys (e.g. `allow_bypass`); `GET` returns the default. Frontend — settings dropdown
GET/PUT wiring; `npm run lint` + `build`. Full backend gate.

## 4. Activation / dormancy

- **P3a** — inert on the live path (no `write_todos` frames on legacy); lights up when the deep path
  runs `write_todos` (Step-10).
- **P3b** — **the one live change** (frontend picker options + plan-preview removal). Backend
  legacy-path behavior byte-identical (`mode` defaults to `"ask"` as before; pinned callers
  untouched). `permission_mode` itself is inert on legacy until cutover.
- **P3c** — the resolved `permission_mode` is inert on legacy until cutover; the default storage +
  GET/PUT + settings dropdown are live but affect only what value is sent/stored, not live behavior.

**No Alembic migrations. Legacy per-step path stays ungated by design. Autonomous path untouched.
Not pushed/merged.**

## 5. Safety invariants (must hold)

- **The legacy chat path remains ungated by design.** P3 does NOT add a synchronous write gate to
  legacy chat. `permission_mode` gating stays deep-path-only.
- **Pinned callers cannot receive a workspace-default-derived authority escalation.** The per-ws
  default (esp. `bypass`) is resolved at the interactive handler only; `schedule_dispatch` /
  `routes_ws` pass explicit/default `permission_mode` and never read the default. `bypass` still
  additionally requires `workspace_allows_bypass` + `deep_single_lead` + `can_pause` +
  `runtime=="deep"` to take effect (all off in prod).
- **`mode` internal semantics unchanged** — the `:472/:540/:649` branches and the pinned callers'
  `mode="execute"`/`"ask"` behavior are byte-identical; only the user-facing request field is removed.
- **JSONB writes merge, not clobber** — `PUT default_permission_mode` must not drop
  `Workspace.settings["allow_bypass"]` or other keys.

## 6. What P3 does NOT do (→ Step-10 / R1)

- Delete the internal `mode` param + its `:472/:540/:649` branches → **Step-10 cutover** (when the
  legacy per-step path is retired).
- Activate the deep single-lead path for chat → **Step-10** (gated, irreversible).
- CLAUDE.md two-execution-paths invariant rewrite → **R1** (the eventual merge).

## 7. Open questions / risks (resolve at build)

- **GET/PUT endpoint home** — new `routes_workspace_settings` module vs folding into an existing
  workspace route. Decide at build (favor a small dedicated module for clarity).
- **`ChatRequest.permission_mode` optionality vs P3b** — P3b removes `mode`; P3c makes
  `permission_mode` optional. Sequence so P3b lands with `permission_mode` still `= "auto"` (required
  by callers) and P3c flips it to `Optional[...] = None` + adds handler resolution. Confirm no caller
  breaks on the `None` default.
- **Frontend store seed timing** — the picker seeds from the workspace default `GET`; before the GET
  resolves, use a lazy `useState` fallback (`"auto"`) per the frontend hook rules (no setState in
  effect). Confirm no hooks-order / render-side-effect violation.
- **`write_todos` frame shape** — re-verify at build that the `tool_call` frame's `input.todos`
  carries the full list each call (content + status) and that suppressing the generic chip for
  `write_todos` doesn't hide a needed `tool_result` error signal.

## 8. Build record (COMPLETE — 2026-07-19)

Built subagent-driven with context-economy: the main loop owned all hot-file mutation + verify +
commit; grounding (3 read-only scouts + direct re-reads) and per-phase security+quality reviews
delegated to read-only subagents. Full gate at every commit checkpoint; each phase committed green
before the next. NOT pushed/merged. 12 commits `1394fe3`..`097b20b`.

- **P3a (`1394fe3`/`bcbc390`/`fee91d2`/`acbf46c`)** — frontend-only inline `write_todos` checklist.
  `todosFromToolCall` pure helper + `ChatTodos` component + chat-panel interception. **Verified at
  build:** BOTH `tool_call` and `tool_result` frames carry `event.tool` (`stream_adapter.py:199/228`),
  so the interception is unambiguous and never misattaches a `write_todos` result to another chip —
  which kept P3a frontend-only (a missing `tool` field would have forced a backend change). Content is
  a React-escaped text child (no XSS). Reviews: security SHIP, quality APPROVED (2 nits applied — drop
  a redundant cast, +1 non-array test).
- **P3b (`d5b9942`/`2b7ac46`)** — retire `mode` → `permission_mode`. Backend drops `mode` from
  `ChatRequest` and forwards a fixed `mode="ask"` (matches the prior default, pinned callers
  byte-identical). Frontend swap was **ATOMIC (one commit)** — the store rename breaks every consumer,
  and the pre-commit full-project `tsc` gate requires a type-clean tree, so the plan's 3-commit split
  collapsed to one. The **build (not grep) caught a 6th bare-`mode` site** (a `command-composer`
  placeholder) that a `\.mode\b` sweep missed — a rename is only done when the type-checker agrees.
  Reviews: security SHIP (pinned callers + ungated-legacy verified), quality APPROVED.
- **P3c (`d08810a`/`c1e29f1`/`a547e66`/`ec2b9cd`/`0cf7c1c`/`097b20b`)** — per-workspace default,
  backend-authoritative. `workspace_default_permission_mode` helper (JSONB, fail-safe auto) beside
  `workspace_allows_bypass`; new `routes_workspace_settings` GET/PUT (`_merged_settings` new-dict
  preserves `allow_bypass`); `ChatRequest.permission_mode` → `Optional[...] = None` +
  `_resolve_request_permission_mode` at the **interactive handler only** (never `_process_core`, so a
  workspace `bypass` default cannot leak onto pinned/scheduled turns). Frontend: api.ts GET/PUT,
  PolicyTab section, settings-modal wiring, chat-panel seeds the picker once per app load. **The full
  gate caught a stale C-SEC3 test** (`test_chat_single_lead` asserted the old `=="auto"` default +
  passed a removed `mode=` kwarg) → updated to assert the still-true invariant (raw default never
  bypass; mode-independence). Reviews: security SHIP (6 invariants incl. no-leak + JSONB-merge +
  bypass-double-gated), quality APPROVED (2 comment-level nits applied), FINAL HOLISTIC review SHIP.

### §7 open-question resolutions
1. **GET/PUT home** — new dedicated `routes_workspace_settings` module (chosen for clarity).
2. **optionality vs P3b** — sequenced exactly as planned: P3b left `permission_mode = "auto"`; P3c
   Task 10 flipped it to `Optional[...] = None` + handler resolution. No caller broke (the stale
   single-lead test was updated, not a real break).
3. **seed timing** — the chat-panel seed uses a one-shot effect + module-level `permissionSeeded`
   guard + a `cancelled` flag; the store default (`"auto"`) is the pre-GET fallback. No hooks-order /
   render-side-effect violation.
4. **`write_todos` frame shape** — confirmed: `input.todos` carries the full list each call; the
   `tool_result` for `write_todos` also carries `tool`, so its chip is suppressed cleanly.

### What P3 does NOT do (→ next)
- **R1** — CLAUDE.md two-execution-paths invariant rewrite → at the merge/cutover (NOT before; doing it
  pre-cutover would describe a dormant path as the live invariant).
- **Step-10 — the runtime cutover** — the only live/irreversible step: push/merge the whole rebuild to
  `main`, flip `JARVIS_RUNTIME=deep` + `deep_single_lead` + `JARVIS_CHAT_PLANLESS` live, run deep
  against prod with shadow-compare + rollback. Needs its own brainstorm → design → plan (plan-per-step
  model) and explicit sign-off at each irreversible gate. This lights up everything P3 built (the
  picker becomes functional, `write_todos` fires, the per-ws default takes effect).
