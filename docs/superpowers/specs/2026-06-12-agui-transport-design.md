# Spec 3A — AG-UI Transport Foundation

Date: 2026-06-12
Program: Week 3 standards adoption (see [oss-release-audit-and-roadmap](2026-06-12-oss-release-audit-and-roadmap.md))
Status: Design — pending review
Sequencing: **3A (this spec)** → 3B (MCP Apps artifact surfaces) → later: Google A2UI typed surfaces (v0.9.1)

## Why

Jarvis emits agent→UI updates over a **homegrown three-channel transport**: per-request chat
SSE (`POST /v1/jarvis/chat`, ~14 ad-hoc event types), a persistent WebSocket
(`WS /ws/{user_id}` relaying Redis `surface` / `surface_update` / `action_result` / `heartbeat`),
and Redis pub/sub as the internal bus. This is a private reinvention of **AG-UI** (the
Agent-User Interaction Protocol) — an open, typed, SSE-based event stream that is the standard
envelope for agentic UIs and the carrier that MCP Apps (3B) and Google A2UI (later) ride inside.

Adopting AG-UI replaces the bespoke wire format with a typed, versioned standard, shrinks
execution payloads (snapshot + JSON-Patch deltas instead of full re-sends), and is the
prerequisite for 3B's tool-based generative-UI delivery. This spec migrates the **transport
only** — the React rendering of typed surfaces (`run`, `proactive_insight`) is unchanged here
and is converted to Google A2UI in a later spec.

## Goals

- Replace the homegrown SSE event shapes and the `SurfaceUpdate`/WS surface relay with **AG-UI
  events** (`ag_ui.core` types, `EventEncoder` SSE serialization).
- Interactive runs stream AG-UI events from a new run endpoint; background/proactive runs emit
  AG-UI events onto a retained ambient channel.
- Re-express the execution surface as AG-UI `StateSnapshot` + `StateDelta` (RFC-6902 JSON Patch).
- Present the TrustEngine approval gate as an AG-UI interrupt **at the API boundary**, while
  keeping Jarvis's durable checkpoint + scheduler resume underneath.
- Keep `JarvisOrchestrator` and `GraphExecutor` frozen — all new behavior lives in injected
  collaborator services and a new API router.

## Non-Goals

- No change to *how typed surfaces render* in React (that is the later A2UI spec).
- No MCP Apps / artifact HTML work (that is 3B).
- No replacement of Jarvis's durable approval/resume mechanism (checkpoint + `resume_run()`).
- No adoption of CopilotKit. We use the vendor-neutral `@ag-ui/client` + `@ag-ui/core` directly.
- No reliance on AG-UI features absent from the pinned Python SDK (interrupts/activity are
  treated as forward-looking; see Risks).

## Decisions (approved 2026-06-12)

1. **Connection model: per-run SSE + retained ambient channel.** Interactive chat runs use an
   AG-UI per-run SSE stream (`POST → text/event-stream`). The **existing WebSocket**
   (`WS /ws/{user_id}`) is retained as the ambient channel for pushes with no active request
   (proactive insights, background-run progress/completion), but its relayed payloads are
   **re-encoded as AG-UI events** instead of `surface` / `surface_update`. Both pipes speak
   AG-UI; the frontend decodes both with one reducer.
2. **Approval model: durable resume, AG-UI facade.** Keep `checkpoint` + scheduler
   `resume_run()` (survives restarts; works for background runs with no open stream). Emit the
   approval as an AG-UI interrupt-shaped event; accept the decision via a resume POST shaped like
   `RunAgentInput.resume[]`; map it onto the existing `approve_action()` path.

## Architecture

### Component placement (god objects stay frozen)

```
api/routes_agui.py            NEW router — POST /v1/agui/run (per-run SSE), POST /v1/agui/resume
api/routes_ws.py              RETAINED as the ambient channel — same WS endpoint, but relayed
                              payloads become AG-UI event frames (not surface_update / surface)
services/agui/translator.py   NEW AguiEventTranslator — maps internal events → typed AG-UI events
services/agui/state.py        NEW ExecStateProjector — builds StateSnapshot + computes StateDelta
                              (RFC-6902) for the execution-state document
services/agui/encoder.py      NEW thin wrapper over ag_ui.encoder.EventEncoder (SSE framing)
orchestrator/contracts.py     SurfaceUpdate / StepState etc. retained during dual-emit, deleted
                              at end of phasing. No new methods on JarvisOrchestrator.
```

`process_message_stream()` keeps yielding its internal event dicts. The **router** pipes those
through `AguiEventTranslator` + `EventEncoder` — no new methods are added to the orchestrator hub.
`GraphExecutor` keeps calling its existing emit hook; the hook is re-pointed (via the injected
collaborator, not a new method) at the translator instead of the `SurfaceUpdate` serializer.

### Event mapping (internal → AG-UI)

| Internal (today) | AG-UI event |
|---|---|
| `text_delta`, `thinking`, `response` | `TEXT_MESSAGE_START/CONTENT/END` (`role: assistant` or `reasoning`) |
| `agent_start` / `agent_done` | `STEP_STARTED` / `STEP_FINISHED`; tokens/cost as a `CUSTOM` event |
| `tool_call` / `tool_result` | `TOOL_CALL_START/ARGS/END` / `TOOL_CALL_RESULT` |
| `intent`, `plan`, `plan_ready` | `STATE_SNAPSHOT` (plan + initial execution-state doc) |
| `surface_update` (phase + `StepState[]`) | `STATE_DELTA` (RFC-6902 patches to the exec-state doc) |
| `user_actions` | `CUSTOM` (`name: "user_actions"`) until A2UI spec models it natively |
| `done` | `RUN_FINISHED` |
| `error`, `step_error` | `RUN_ERROR` (run-fatal) / `STATE_DELTA` (step-local error field) |
| `approval_needed` + `ApprovalContext` | interrupt-shaped `RUN_FINISHED` outcome (see Approval) |
| WS `surface` (workspace push) | `STATE_SNAPSHOT` on the ambient stream (typed surface state) |
| WS `heartbeat` | SSE comment ping / AG-UI keep-alive |

Every emitted event is a typed model (the SDK's `ag_ui.core` event classes, matched on the
`type` discriminator) — satisfying "contracts at every boundary" and "discriminated unions over
type-sniffing."

### Execution-state document (StateSnapshot / StateDelta)

A single canonical JSON document per run, carried by snapshot+delta:

```json
{
  "runId": "run_…", "threadId": "…",
  "phase": "planning|plan_ready|executing|approval_needed|completed|failed|partial",
  "goal": "…", "progress": "3/5",
  "steps": [
    {"step_id": "…", "description": "…",
     "status": "pending|executing|completed|failed|approval_needed|user_action",
     "output_summary": null, "duration_ms": null, "error": null, "retry_count": 0}
  ],
  "approval": null,
  "results": {"key_findings": [], "artifacts_created": [], "suggested_next": []}
}
```

`ExecStateProjector` emits one `STATE_SNAPSHOT` at `plan_ready`, then `STATE_DELTA` RFC-6902
patches on every transition (`{"op":"replace","path":"/steps/2/status","value":"completed"}`,
`/progress`, `/phase`). Frontend applies patches atomically (`fast-json-patch`); on inconsistency
it re-requests a snapshot. This replaces the full-`StepState[]`-resend in today's `surface_update`.

### Approval / HITL (durable resume, AG-UI facade)

1. TrustEngine halts a step → `GraphExecutor` (via the injected emit collaborator) produces an
   interrupt-shaped terminal event for that run:
   `RUN_FINISHED` with `outcome = {type: "interrupt", interrupts: [{interruptId: <approval_id>,
   reason: "tool_call", approval: <ApprovalContext>}]}`. The exec-state doc `phase` →
   `approval_needed`, `approval` populated. For background runs (no open stream) the same event
   is delivered on the ambient channel and persisted (as today, to `UISurface.payload`).
2. Frontend renders the approval UI (unchanged component) from the interrupt payload.
3. User decision → `POST /v1/agui/resume` with `{threadId, runId,
   resume: [{interruptId, status: "resolved"|"cancelled", payload: {approved, editedArgs?}}]}`.
4. Router maps `resume[]` onto the existing `approve_action()` / `reject_action()` /
   `edit_approval()` services → sets `source="approval_resume"` on the `TaskRun` → scheduler
   picks it up → `GraphExecutor.resume_run()` continues from `checkpoint`.
5. The resumed run emits a fresh AG-UI event stream on the ambient channel (interactive callers
   may also receive the continuation as the resume POST's SSE response).

If the pinned Python SDK lacks the `RunFinished.outcome` interrupt union, the facade emits the
interrupt as a typed `CUSTOM` event with the same payload — the API boundary shape is identical,
so the frontend and a future SDK upgrade are unaffected.

## Packages & versions

- Backend: `ag-ui-protocol` (PyPI; import `ag_ui`; `EventEncoder` for SSE). Pin the version;
  build strictly against its actual `EventType` enum. Python ≥3.9 ✓ (Jarvis is 3.12).
- Frontend: `@ag-ui/core` + `@ag-ui/client` (`HttpAgent`), MIT, React 19 ✓. No CopilotKit.
- Existing Zustand `surface-store` is retained as the state sink; its `updateSurface()` is
  re-implemented to apply AG-UI `StateDelta` patches instead of `SurfaceUpdate` merges.

## Internal phasing (de-risk; structure/behavior commits separated)

1. **Characterization tests (behavior pin).** Snapshot the current chat-SSE event sequence,
   the `surface_update` phase progression, and the approval→resume flow (backend) plus the
   frontend reducer's surface-store transitions. These must stay green through phase 4.
2. **Dual-emit (structure, no behavior change).** Add `services/agui/*` + `routes_agui.py`.
   New endpoints emit AG-UI events; the legacy `/v1/jarvis/chat` SSE and WS `surface_update`
   keep working unchanged. Backend now produces both formats.
3. **Frontend switch.** Chat panel consumes `HttpAgent` (per-run SSE); ambient consumer decodes
   AG-UI events; `surface-store` applies `StateDelta`. Delete the manual SSE parser in `api.ts`.
4. **Delete legacy (behavior-preserving).** Remove old SSE event shapes, the WS `surface_update`
   message + relay path, the `SurfaceUpdate`/`StepState`-as-wire contracts, and dual-emit. Keep
   Redis as the internal bus behind the ambient channel.

## Testing strategy

- Unit: `AguiEventTranslator` mapping table (each internal event → expected AG-UI event);
  `ExecStateProjector` snapshot + delta correctness (RFC-6902 round-trips); encoder SSE framing.
- Integration: `POST /v1/agui/run` emits a valid `RUN_STARTED … RUN_FINISHED`-bracketed stream;
  approval emits the interrupt outcome; `POST /v1/agui/resume` drives `resume_run()`.
- Frontend: reducer applies a snapshot then deltas to the correct final surface state; approval
  interrupt renders the approval card; resume POST shape is correct.
- Characterization tests from phase 1 remain the regression guard.
- Coverage target ≥80% on new `services/agui/*` and `routes_agui.py`.

## Rename scope (this spec)

3A's rename footprint is small: the `SurfaceUpdate` wire contract is deleted outright, and the
Redis channel `jarvis:a2ui:{user_id}` is renamed off the "A2UI" collision (e.g.
`jarvis:surface:{user_id}`). The larger rename of the rendering layer (`components/a2ui/`,
`useSurfaceStore`, `src/ui/renderer.py`) lands in 3B where those files are already churned.

## Risks

- **Pre-1.0 Python SDK churn** (`ag-ui-protocol` is 0.1.x; interrupts/activity may be draft or
  absent). Mitigation: pin the version, build against its real `EventType` enum, and isolate all
  SDK-version-sensitive shapes (esp. the interrupt outcome) behind `services/agui/translator.py`
  so an upgrade is a one-module change. The approval facade explicitly does not depend on the
  interrupt union existing.
- **Two transports retained** (per-run SSE + ambient) — accepted trade-off (Decision 1). Both
  carry AG-UI events and share one frontend decoder, so it is one protocol over two pipes, not
  two protocols.
- **God-object pressure.** Re-pointing `GraphExecutor`'s emit hook must be done via the injected
  collaborator, not a new method on the frozen hub — enforced in review.

## What 3B depends on from 3A

- A working AG-UI event stream that can carry **tool-based generative-UI** payloads (the seam
  MCP Apps resources ride in).
- The ambient channel delivering AG-UI events for surfaces (3B artifacts are pushed as state /
  tool-result events carrying `ui://` resource references).
- The renamed surface channel + retained Redis bus.
```
