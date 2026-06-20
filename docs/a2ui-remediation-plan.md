# A2UI End-to-End Remediation Plan

> Status: IMPLEMENTED — 2026-06-21. Branch: `review/architecture-remediation` (committed 4740678→3cdbe0d, not pushed).
> Scope: backend + frontend + infra. Driven by screenshot-observed defects and a 5-agent code audit.
> All 5 phases landed, each review-gated; full non-e2e backend suite 2683 green, frontend 85 green.
> No Alembic migration required (new entity types validate in Python; artifact_refs/trace use existing JSONB/columns).

## Outcome (per phase)
- **P1 (4740678)** approvals actionable: run-surface Approval tab + REST-wired buttons + artifact_refs preview; cross-tenant ownership guard on ephemeral detail.
- **P2 (12df814)** clean text: event_id/pipeline-jargon stripped from insight titles + briefing memory.
- **P3 (c3c8c39)** trace: token/cost rollup at pause (accumulated across resume segments) + honest empty-state.
- **P4 (7acd6f1)** linkage+UI: briefing fallback-to-most-recent fixes "No linked briefing"; run-detail modal on Jarvis design tokens; shared step renderer.
- **P5 (3cdbe0d)** entity quality: financial_transaction/merchant types + paid_to/charged_to; bare-email-as-name PII guard.

## 1. Symptoms → Root Causes (audited, with file:line)

### D1 — Raw event identity & pipeline jargon leak into user-facing text
Insight cards read `Polled gmail: 1 new event(s). [gmail] email_received: INR 1087 ... (event_id=evt_01KVK...)`.

- `backend/src/orchestrator/connector_poller.py:301-303` — builds `[{source}] {event_type}: {title}` and **appends `(event_id=evt_...)`** to a human-readable string.
- `backend/src/orchestrator/perception_runner.py:262-264` — aggregates those into `observer_summary = f"Polled {source}: {N} new event(s).\n- ..."`. This is meant for **agent** (Librarian/Planner) consumption.
- `backend/src/orchestrator/perception_runner.py:324-328` — stuffs that raw `observer_summary[:500]` into `PerceptionSignal.summary`.
- `backend/src/orchestrator/surface_pusher.py:337-339` — uses `signal.summary[:120]` as the insight **title** (raw) while `assessment.reasoning[:200]` is the **subtitle** (clean LLM prose). The split is the visible inconsistency.
- `backend/src/orchestrator/perception_runner.py:391-398` — briefing **memory** text is also polluted with the raw `observer_summary` prefix.

**Root cause:** the internal "observations for the Librarian" string is conflated with the user-facing insight title/memory. No clean, human-authored title field exists for insight surfaces.

### D2 — Approvals are not actionable (top complaint)
- `frontend/.../execution-surface.tsx:71-78` — `InlineApprovalCard` renders **only** when `phase === "approval_needed"` with a live `approval` context, delivered transiently via WS from `backend/.../trust_gate.py:157-182`.
- The **persisted** Run detail modal (`frontend/src/components/history/run-detail-modal.tsx`, tabs Steps/Plan/Events/Trace) has **no approval tab and no approve action**. The workspace surface-detail modal also has no approval action.
- `backend/src/api/routes_history.py:571-605` — `/v1/runs/{run_id}/resume` only sets `source="approval_resume"` and returns; actual resume waits for the scheduler tick (`scheduler/background_tasks_tick.py:79-86`), ≤30s+ latency. No synchronous resume.
- `backend/src/services/trust_gate.py:102-114` — step-level approvals are created with `artifact_refs=None` (violates CLAUDE.md "approval needs run_id + artifact_refs"); the approval has no preview of *what* will be done.

**Root cause:** the only place a user can approve is a live socket frame. Reload/detail/history views are dead ends, and resume is async-only.

### D3 — Trace tab is all zeros (tokens/cost) despite real duration
- `backend/src/orchestrator/tracing.py:197-199` — `TraceManager.finish_trace()` calls `store_trace(...)` **without `run_id`** on the chat path, so `Trace.run_id` is NULL.
- `backend/src/services/surface_detail_builders/run.py:219-230` — trace tab looks up `Trace` by `run.trace_id`, then reverse `Trace.run_id == run.run_id`, then falls back to TaskRun denormalized columns (`input_tokens=0`). All three miss for chat-originated surfaces.
- Global budget ($1.28/$25) works because `TokenUsage` is aggregated by `workspace_id`+date independent of run linkage.

**Root cause:** trace records are persisted with correct tokens but **not linked to the run** the trace view fetches (`run_id` never threaded through on the relevant path).

### D4 — UI/UX inconsistency & broken data linking
- Two parallel modal systems: `run-detail-modal.tsx` uses **hardcoded GitHub colors** (`#161b22`, `#8b949e`) vs `surface-detail-modal.tsx` uses Jarvis design tokens.
- Three different step renderers (`step-list.tsx`, `execution-trace.tsx`, run-detail-modal Steps tab) with divergent styling.
- "No linked briefing found" — `surface_detail_builders/briefing.py` finds no `Briefing` for (user_id, today); no UNIQUE constraint / lookup is date-fragile.
- Archived runs get a 2h TTL (`execution_surface_emitter.py`), so run detail lookups fail after 2h.

### D5 — Entity extraction quality (lower priority, larger)
- `backend/src/services/world_model.py:91-133` — 21 entity types but **no financial/transaction semantics** (amount, merchant, currency, account); these become untyped `attributes`.
- PII risk: prompt falls back to using the email address as canonical name.
- Memory content inherits the raw `observer_summary` (see D1).

## 2. Phased Plan

Ordering by user impact and blast radius. Each phase ends with `pytest` green + a review subagent.

### Phase 1 — Make approvals actionable end-to-end (D2)  ★ highest impact

**Locked action contract (verified 2026-06-21):**
- `units.approval_card` buttons emit `A2UIAction(type="click", payload={"type":"approval.{approve|reject|edit}","approval_id":...})`.
- Frontend `A2UIButton` calls `onAction(action.type="click", action.payload)`. The modal's `onAction` MUST branch on `payload.type` and route `approval.*` to REST (`approveAction/rejectAction/editApproval` in `lib/api.ts` → `POST /v1/approvals/{id}/...`), NOT to WS. Non-approval actions keep going through `handleA2UIAction` (WS).
- The live `InlineApprovalCard` keeps its working WS path (`sendAction("approve",{id})`).
- Backend run surface must expose an `approval` detail tab (rendered via `units.approval_card`) so the card appears on the persisted run the user opens; default the modal to it when `status==awaiting_approval`.


1. Backend: emit a **durable** `approval`-kind surface (or persist approval context into the run surface payload) so it survives reload; ensure `detail_config` for run surfaces includes an **Approval tab** with approve/reject/edit actions when `status==awaiting_approval`.
2. Backend: populate `artifact_refs` (tool + params preview) for step-level approvals in `trust_gate.py` so the user sees *what* they're approving.
3. Backend: add a **synchronous resume** path — after `approve_action`, either inline-resume or wake the scheduler immediately (e.g. Redis nudge) so the run doesn't sit ≤30s.
4. Frontend: render an approval action in BOTH the workspace surface-detail modal and the run-detail modal (reuse `InlineApprovalCard`), wired to existing `POST /v1/approvals/{id}/approve|reject|edit`. Add optimistic state transition + refetch.
5. Frontend: surface the global approval queue ("2 approvals") as a clickable entry that lists pending approvals.

### Phase 2 — Clean user-facing text (D1)
1. Backend: stop appending `(event_id=...)` to human strings in `connector_poller.py`; keep event_id in a structured field, not prose.
2. Backend: give insight surfaces a clean title — prefer a short LLM/assessment-authored headline over raw `observer_summary`. Use `assessment.reasoning`/a derived headline for the title; keep raw observations only in agent-facing context.
3. Backend: de-pollute briefing memory text (`perception_runner.py:391-398`).

### Phase 3 — Fix trace linkage (D3)
1. Backend: thread `run_id` through `finish_trace → store_trace` for run-backed flows; ensure `run.trace_id` is always set.
2. Backend: backfill TaskRun denormalized token/cost columns on completion as a robust fallback.
3. Frontend: verify trace tab reads the populated fields; add a clear empty-state when a surface legitimately has no LLM work.

### Phase 4 — UI/UX unification & data linking (D4)
1. Frontend: migrate `run-detail-modal.tsx` off hardcoded colors onto Jarvis design tokens; consolidate step rendering into one component.
2. Backend: fix briefing linkage (idempotent lookup / today-or-latest); reconsider 2h archive TTL for run detail.

### Phase 5 — Entity extraction quality (D5)  (stretch)
1. Add financial/transaction entity types + structured attributes; remove PII-as-name fallback or gate it.

## 3. Execution model
- Subagent-driven: one implementation subagent per workstream with a precise rewrite map; main loop owns design/spec/verify/commit.
- After each phase: run `pytest tests/ -v` (backend) + `npm run lint`/`npm run build` (frontend); dispatch a review subagent; commit structure/behavior separately.
- Verify the approval loop live via the preview tooling before claiming Phase 1 done.
