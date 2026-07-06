# Step 6B — The One Approval Gate (Deep Chat Runtime) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the single deterministic approval gate to the now-live Deep Agents chat runtime (behind `JARVIS_RUNTIME=deep`): a `wrap_tool_call` + `interrupt()` gate middleware that **short-circuits (ungated) for `authorization_source="direct_user_request"`** and, for non-direct provenance, evaluates `TrustEngine` × `RiskAssessor` **plus an IRREVERSIBLE hard override** → pauses the turn via `interrupt()` when approval is required; a **stable per-turn `thread_id`** persisted in the `Approval` record; a backend **resume path** that re-enters the paused graph via `Command(resume=…)`; and a `stream_adapter` that surfaces an **`approval_needed`** SSE frame instead of a generic error. **Dormant on live direct-chat traffic** (all chat writes stay `direct_user_request` → ungated = today's behavior); proven via a **forced-provenance** end-to-end test. **No frontend, no migration.**

**Architecture:** A new `deep_runtime` gate middleware sits **between `capability_scope` (outer) and `jarvis_tool_dispatcher` (inner)** in the `wrap_tool_call` chain. `authorization_source` is a **phase-1 literal** captured in the seam (always `"direct_user_request"` on the chat path today; the gate accepts other values and is proven via a forced-provenance test). The gate reuses `TrustEngine.evaluate` + `RiskAssessor.get_or_assess_risk` + the existing `verification/predicate.py` IRREVERSIBLE predicate. When it must pause, it persists an `Approval` (thread_id in `artifact_refs`, **no migration**) and raises `interrupt()`; the durable `AsyncPostgresSaver` (wired in 6A.5) checkpoints the graph; `stream_deep_agent_events` catches the interrupt and emits `approval_needed`. A backend resume method re-enters via `Command(resume=…)` on the stored `thread_id`, re-streaming the continuation. Live behavior is unchanged because the gate short-circuits every direct-user write.

**Tech Stack:** Python 3.12; deepagents 0.6.11 / langgraph 1.2.6 / langgraph-checkpoint-postgres 3.1.0 / langchain 1.3.10 / langchain-core 1.4.8 / langchain-anthropic 1.4.6; async SQLAlchemy over asyncpg; pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio).

---

## Infra note (verify at start)

Run all commands from `backend/` via `uv run`:

```bash
docker compose up -d postgres redis qdrant     # from repo root
cd backend
uv sync --all-extras                            # NO pip; plain `uv sync` drops dev extras
uv run alembic upgrade head                     # head c7d3e4f5a6b8 (6B adds NO migrations)
uv run pytest tests/ --ignore=tests/e2e         # baseline: 3150 passed / 18 skipped (after 6A.5 + CF batch)
```

- **NO pip.** No new dependencies — all libraries above are installed.
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs.
- **No migrations.** The `thread_id` is stored in the existing `Approval.artifact_refs` JSONB column; the gate reuses existing tables (`approvals`, `trust_state`, `trust_ceiling`, and langgraph's `checkpoints*` created at runtime by `AsyncPostgresSaver.setup()` and excluded by `alembic/env.py._include_object`). `alembic check` must stay drift-free.
- **API key:** all pytest tests use fake models / a local Postgres — no API key. Only the OPTIONAL live smoke (Task 9) needs `JARVIS_ANTHROPIC_API_KEY`.
- **Default stays `legacy`.** Every gate change is gated on `runtime=="deep"` or inert on the legacy path. **AND, on the deep path, the gate short-circuits for `direct_user_request`** — so `JARVIS_RUNTIME=legacy` AND live direct chat on `JARVIS_RUNTIME=deep` both stay byte-behavior-identical. The gate only becomes active for `authorization_source ∈ {autonomous, headless, custom}`, which is proven via a forced-provenance test and does NOT occur on live chat traffic in 6B.

---

## Current-state (verified 2026-07-07 by 4 parallel code-extraction passes against HEAD `313a1f8` + installed source + two runnable spikes)

### Interrupt / resume topology
1. **The Step-0 spike proved DECISION-A** (`backend/spikes/interrupt_in_wrap_tool_call/probe.py`): `interrupt({...})` called inside a `@wrap_tool_call` middleware BEFORE `await handler(request)` pauses the graph; the tool body runs only on the resumed pass. Resume = `await agent.ainvoke(Command(resume="approve"), config=config)` with the SAME `thread_id`; the `interrupt()` call RETURNS the resume value. Import: `from langgraph.types import Command`.
2. **Composition is clean.** `build_deep_agent` (`src/deep_runtime/agent_builder.py:97-118`) installs `capability_scope_guard` first (when `db_factory`), then appends `extra_middleware`. First-in-list = outermost. So passing `extra_middleware=(trust_gate, dispatcher)` yields the chain **capability_scope (OUTER) → trust_gate (MIDDLE) → jarvis_tool_dispatcher (INNER)** — the gate's `interrupt()` fires BEFORE the dispatcher's `execute_tool`, so no external write happens on the interrupted pass. `capability_scope.py:102` and `jarvis_tool_dispatcher.py:65-66` both call `handler`/`execute_tool` WITHOUT a blanket try/except, so `GraphInterrupt` propagates. Preserve that.
3. **`durability="sync"` is an `ainvoke`/`astream` kwarg** (from the AsyncPostgresSaver spike `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md`), NOT a `compile()` arg. The initial (interrupting) call must use `durability="sync"` so the checkpoint is committed to Postgres BEFORE the `approval_needed` frame reaches the client — otherwise a resume REST call could race the checkpoint write.
4. **`stream_deep_agent_events` currently SWALLOWS the interrupt** (`src/deep_runtime/stream_adapter.py:131-194`): the entire `agent.astream(stream_mode=["messages","updates"])` loop is inside `try: … except Exception as exc:` → a `GraphInterrupt` (subclass of `Exception`) is caught → a generic sanitized `error` frame is emitted and the stream returns. 6B must add `except GraphInterrupt` BEFORE the generic `except`. **VERIFY at spike time (Task 0)** whether the installed langgraph ALSO surfaces `{"__interrupt__": (Interrupt(...),)}` in the `updates` payload before/instead of raising — handle whichever occurs.

### The gate decision pieces (all EXIST — 6B only wires them)
5. **`TrustEngine.evaluate`** (`src/services/trust_engine.py:105-128`): `async def evaluate(self, capability: str, risk_assessment: RiskAssessment, workspace_id: str | None = None) -> PolicyDecision`. 4×4 matrix (trust_level × risk_level) → `PolicyDecision.decision ∈ {"auto_execute_notify","auto_execute_silent","approval_required"}` (fail-closed fallback = `approval_required`). Takes **no** `authorization_source`.
6. **`RiskAssessor`** (`src/services/risk_assessor.py`): `get_or_assess_risk(...)` Redis-cached 24h → `RiskAssessment(risk_level, reasoning, reversible, blast_radius)`. **Fail-closed default `risk_level="high"`, `reversible=False`** on any exception (`:116`). NOTE: the `assess_risk` docstring (`:83`) says "medium" — STALE; the code fails to `high`. Do not "fix" the code to medium.
7. **IRREVERSIBLE predicate** (`src/services/verification/predicate.py`): `IRREVERSIBLE(*, reversible, blast_radius) -> bool` (`:23`); `is_irreversible_capability(capability) -> bool` (`:57`, fail-closed: unknown write ⇒ irreversible); `is_write_verification_required(capability, risk) -> bool` (`:69`, the UNION of the static classifier and the per-step RiskAssessment). The module docstring (`:7-11`) explicitly pre-declares the **dual Step-3/Step-6 use** — 6B imports it, no new predicate.
8. **The autonomous gate model** (`src/services/dag_runner.py:337-346`): `risk = trust_gate.assess_step_risk(...)`; `decision = trust_engine.evaluate(capability, risk, workspace_id)`; if `approval_required` → `trust_gate.create_approval_and_pause(...)` (persists `Approval` with `run_id`+`artifact_refs`, transitions the run to `awaiting_approval`, breaks the DAG loop). **Resume** (`routes_approvals.py:247`): sets `run.source="approval_resume"` → the SCHEDULER picks it up → `GraphExecutor.resume_run`. This is a DAG/TaskRun model — the chat path has NO TaskRun, so 6B uses a DIRECT (synchronous) resume via `Command(resume=…)`, NOT the scheduler.
9. **`authorization_source` / `provenance_taint` / `direct_user_request` = ZERO hits in `src/`** (verified). The distinguishing signal that DOES exist is `TaskRun.source` (`"background"`/`"user_message"`/`"approval_resume"`/`"plan"`) — but the deep chat path creates NO `TaskRun`. So authorization is implicit in the entry point. Phase-1 = a literal captured in the seam and passed to the gate closure.

### Durable checkpointer + thread_id (from 6A.5 + CF batch)
10. **Checkpointer is fully wired** (6A.5): `app.py:69-88` builds `app.state.deep_checkpointer` (+ `deep_checkpointer_pool`, + `deep_checkpointer_degraded` from the CF batch) gated on `runtime=="deep"`; `routes_chat._get_orchestrator(settings, app)` builds `provider = lambda: getattr(app.state,"deep_checkpointer",None)`; `JarvisOrchestrator` → `AgentInvoker._checkpointer_provider`; seam uses `checkpointer=self._checkpointer_provider() or MemorySaver()` (`agent_invoker.py:203`).
11. **`thread_id` is ephemeral** (`agent_invoker.py:205`): `config = {"configurable": {"thread_id": generate_id("chat")}}` — FRESH ULID per call, so a paused graph can't be resumed by a separate REST call. `generate_id("chat")` = `f"chat_{ULID()}"` (`src/models/ids.py`). The seam sends ONE message (`graph_input = {"messages":[{"role":"user","content":message}]}`, `:206`); conversation history is injected separately by `context_assembler` — so a per-turn thread_id does NOT need to carry multi-turn history. **Decision (D4): per-turn stable thread_id, persisted in `Approval.artifact_refs["thread_id"]`** — avoids the double-history problem a conversation-keyed thread_id would cause.
12. **`Approval` model** (`src/models/approvals.py`): `artifact_refs` is a nullable JSONB column (add `"thread_id"` with NO migration); `run_id`/`step_id` are nullable (chat interrupt leaves them null); `approval_type`, `title`, `summary`, `risk_level`, `status ∈ {pending,approved,rejected,expired}`. Constructor: `src/services/approval_service.create_approval(...)`.

### Streaming / SSE / scope boundary
13. **The 7 frozen SSE shapes** (`stream_adapter.py`): `agent_start`, `thinking`, `text_delta`, `tool_call`, `tool_result`, `agent_done`, `error`. `routes_chat.chat_stream` maps `CoreEvent` → SSE via `core_event_to_sse` (`core_events.py:284`); the deep frames flow `stream_deep_agent_events` dict → `agent_event_from_sse(evt)` (`core_events.py:240-281`, catch-all `AgentStreamEvent(payload=evt)` — NEVER returns None) → `core_event_to_sse`. 6B surfaces an 8th shape `approval_needed` as a stream DICT frame only (D7); the typed `ApprovalNeeded` CoreEvent + the `core_events.py` arms are DEFERRED to the frontend (lever B), so `core_events.py` is untouched in 6B.
14. **6B/6C boundary (do NOT leak 6C).** 6C-only, MUST NOT touch: `src/services/locking.py` (`RedisLock`/`distributed_lock` — **ZERO callers**, the post-approval write fence); `src/services/capability_resolver.py:137-140` (the write→Operator branch — "kill Operator"); `src/orchestrator/intent_classifier.py` `FAST_INTENTS` (fast-path routing); `routes_approvals.py:185-196` (`record_approval_decision` — user-approval trust-increment relocation). The existing autonomous `approve`/`reject` endpoints (`routes_approvals.py:112,371`) stay as-is — 6B adds a NEW chat resume path, it does not restructure them. `call_agent` (non-streaming perception, `agent_invoker.py:285-357`) is untouched.

---

## Design decisions

- **D0 — Gate policy = NON-DIRECT PROVENANCE ONLY (user-locked 2026-07-07).** The gate SHORT-CIRCUITS (ungated, execute now) when `authorization_source == "direct_user_request"`. It evaluates (trust×risk + IRREVERSIBLE override) only for `authorization_source ∈ {autonomous, headless, custom}`. On the live deep chat path today, the seam always passes `direct_user_request` → the gate is **dormant** (byte-behavior-identical to today). The machinery is BUILT and PROVEN via a forced-provenance test; it activates when 6C/Step-10 brings autonomous/headless traffic onto the deep runtime. This honors the two-execution-paths invariant ([[project_inline_trust_gap]]).
- **D1 — Gate = `wrap_tool_call` + `interrupt()` (spike DECISION-A), a SEPARATE middleware.** Not deepagents' `interrupt_on=`/HITL (that needs a static pre-enumerated tool-name dict; Jarvis gates on capability/irreversibility resolved at call time). The gate is its OWN middleware (mirrors 6A.5's "capability_scope separate from dispatch" separation), placed via `extra_middleware=(trust_gate, dispatcher)` → chain: capability_scope (outer) → trust_gate → dispatcher (inner).
- **D2 — IRREVERSIBLE HARD OVERRIDE via `is_write_verification_required` (the UNION).** On the gated path, if `is_write_verification_required(capability, risk)` is True, force `approval_required` regardless of the trust matrix (even `autonomous`). Reuses `verification/predicate.py` (dual-use pre-declared). This is why an irreversible write is never auto-executed on a gated path.
- **D3 — `authorization_source` = phase-1 literal.** A small `AuthorizationSource` constants set (`direct_user_request`, `autonomous`, `headless`, `custom`). Captured in the seam (currently hardcoded `direct_user_request`) and passed into the gate closure — NEVER LLM-supplied. Phase-2 per-arg taint plumbing is a separate later security plan (OUT).
- **D4 — Stable per-turn `thread_id`, persisted in `Approval.artifact_refs["thread_id"]`.** The seam mints ONE `thread_id` per turn (still `generate_id("chat")`) and passes it to BOTH the graph `config` and the gate closure. On interrupt the gate stores it in the Approval. The resume path reads it back. No migration; no double-history (per-turn, not per-conversation).
- **D5 — Resume = DIRECT `Command(resume=…)` on the deep runtime, NOT the scheduler.** A backend `AgentInvoker.resume_deep_turn(approval_id, decision)` recovers the thread_id from the Approval, rebuilds the deep agent with the SAME `app.state` durable saver, and `astream(Command(resume=decision), {thread_id})` re-streams. This METHOD is the load-bearing resume machinery and is proven **directly by test** (Tasks 6/7 call it). **The HTTP endpoint is DEFERRED** (scope lever A): under D0 the gate is dormant on direct chat, so no caller exists in 6B (no frontend; direct chat never interrupts) — the `POST /v1/jarvis/chat/resume` route lands with the frontend approval UX / 6C activation, when its consumer and real streaming/error contract exist.
- **D6 — `durability="sync"` on the interrupting call** so the checkpoint commits before `approval_needed` is emitted. Added at the seam's `astream` and the resume `astream`.
- **D7 — `stream_adapter` surfaces an `approval_needed` DICT frame** via an `except GraphInterrupt` clause (and, if the installed langgraph yields `__interrupt__` in `updates`, handle that too — verified in Task 0). This is the backend-observable pause signal (Task 6 asserts on it directly off `stream_deep_agent_events`). **The typed `ApprovalNeeded` CoreEvent + the `core_events.py` `agent_event_from_sse`/`core_event_to_sse` arms are DEFERRED** (scope lever B): they only matter for HTTP-layer SSE emission to a client, which nothing consumes in 6B — the frontend adds them when it renders the approval card. `core_events.py` stays out of the 6B blast radius.
- **D8 — NO migration, NO frontend, NO 6C.** Redis write lock, kill-Operator, fast-path routing, trust-increment relocation are all 6C (§ current-state 14). The gate does NOT take the write lock (that's 6C's post-approval fence).

---

## In-flight posture

- Branch `rebuild/first-principles`, HEAD `313a1f8` (after 6A.5 `a56b382` + CF batch `2d92a93`,`313a1f8`). Do NOT push/merge to main.
- Per-task commit (conventional-commit, no `Co-Authored-By`).
- Commit the plan doc BEFORE dispatching implementers; don't let a reviewer's `git stash --include-untracked` orphan untracked files.
- Full gate after each task: `uv run pytest tests/ --ignore=tests/e2e`. Baseline entering 6B = 3150 passed / 18 skipped.
- **Spike-first (Task 0):** the streaming interrupt→frame→`Command(resume)` composition through `build_deep_agent` + `stream_deep_agent_events` is NOT yet proven offline (Step-0 spike used `ainvoke`, not the streaming adapter). Prove it before building, per 6A/6A.5 discipline.

---

## File structure

| File | Change | Task |
|---|---|---|
| `backend/spikes/deep_stream/interrupt_resume_stream_proof.py` | **Create** — offline proof: interrupt inside a `wrap_tool_call` gate within a compiled `create_deep_agent`, streamed via `astream(stream_mode)`, surfaces the interrupt + `Command(resume)` re-streams | 0 |
| `backend/src/deep_runtime/authorization.py` | **Create** — `AuthorizationSource` constants + `is_gated_source()` | 1 |
| `backend/tests/deep_runtime/test_authorization.py` | **Create** | 1 |
| `backend/src/deep_runtime/middleware/trust_gate.py` | **Create** — `make_trust_gate_middleware(...)` (`wrap_tool_call` + `interrupt()`) | 2 |
| `backend/tests/deep_runtime/test_trust_gate.py` | **Create** — short-circuit direct; gated→interrupt; IRREVERSIBLE override; approve/reject verdicts | 2 |
| `backend/src/deep_runtime/stream_adapter.py` | **Modify** — `except GraphInterrupt` → `approval_needed` dict frame (+ `__interrupt__` updates handling if present) | 3 |
| `backend/tests/deep_runtime/test_stream_adapter_interrupt.py` | **Create** | 3 |
| `backend/src/orchestrator/agent_invoker.py` | **Modify** — add `resume_deep_turn(...)` (T4); deep seam: build+wire `trust_gate` (extra_middleware), `authorization_source`, stable `thread_id`, `durability="sync"` (T5) | 4, 5 |
| `backend/tests/test_agent_invoker_resume.py` | **Create** — `resume_deep_turn` recovers thread_id + re-streams | 4 |
| `backend/tests/test_deep_gate_end_to_end.py` | **Create** — forced-provenance guard: gated write → interrupt → approval persisted (thread_id) → resume(approve)=execute / resume(reject)=blocked; direct_user_request → ungated (no interrupt) | 6 |
| `backend/spikes/deep_stream/live_gate_smoke.py` | **Create (OPTIONAL, live)** | 9 |
| `CLAUDE.md` | Doc note: the one gate on the deep runtime (dormant on direct chat) | 10 |

---

## Task 0 (SPIKE): prove the streaming interrupt→frame→resume composition offline

**File:** `backend/spikes/deep_stream/interrupt_resume_stream_proof.py` (a runnable script, not a pytest test).

- [ ] **Step 1: Write the spike.** Mirror `backend/spikes/deep_stream/central_dispatcher_proof.py` (fake scripted streaming `BaseChatModel`) + `backend/spikes/interrupt_in_wrap_tool_call/probe.py` (interrupt/resume). Build a REAL `create_deep_agent` with `checkpointer=MemorySaver()` and a single `@wrap_tool_call` gate middleware whose body calls `verdict = interrupt({"approval_id":"apr_test","reason":"..."})` before `handler(request)`; on `verdict=="approve"` calls `handler`, else returns `ToolMessage(status="error")`. Drive turn-1 with a fake model that calls one write tool.
- [ ] **Step 2: Prove the interrupt surfaces in the STREAM.** Run `agent.astream({"messages":[…]}, config, stream_mode=["messages","updates"], durability="sync")` and record: does a `GraphInterrupt` RAISE out of the loop, and/or does an `{"__interrupt__": (...)}` payload appear in the `updates` mode? PRINT exactly what happens (both are possible depending on version). This decides Task 3's detection strategy.
- [ ] **Step 3: Prove resume re-streams.** After the interrupt, call `agent.astream(Command(resume="approve"), config, stream_mode=["messages","updates"])` on the SAME `thread_id` and confirm the tool now executes (handler runs) and the turn completes (a final AI message). Then repeat with `Command(resume="reject")` on a FRESH thread and confirm the tool does NOT run and a rejection ToolMessage is produced.
- [ ] **Step 4: Document the findings** as a top-of-file docstring (what surfaces in the stream, the exact resume call, `durability` behavior). Commit:
```bash
git add backend/spikes/deep_stream/interrupt_resume_stream_proof.py
git commit -m "spike(rebuild): prove streaming interrupt->approval_needed->Command(resume) for the deep gate (Step 6B research)"
```
- [ ] **If the composition does NOT work as DECISION-A predicts, STOP and escalate** (do not build on an unproven mechanism).

---

## Task 1: `authorization_source` phase-1 constants

**Files:** Create `backend/src/deep_runtime/authorization.py` + `backend/tests/deep_runtime/test_authorization.py`.

- [ ] **Step 1: Write the failing test** — `test_authorization.py`:
```python
"""Step 6B: authorization_source phase-1 — direct_user_request is ungated; the others are gated."""

from src.deep_runtime.authorization import AuthorizationSource, is_gated_source


def test_direct_user_request_is_not_gated():
    assert is_gated_source(AuthorizationSource.DIRECT_USER_REQUEST) is False


def test_autonomous_headless_custom_are_gated():
    for src in (AuthorizationSource.AUTONOMOUS, AuthorizationSource.HEADLESS, AuthorizationSource.CUSTOM):
        assert is_gated_source(src) is True


def test_unknown_source_is_gated_fail_closed():
    assert is_gated_source("something_new") is True
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `authorization.py`:
```python
"""Authorization-source provenance for the deep-runtime approval gate (Step 6B, phase-1).

Phase-1 is a coarse STRUCTURAL rule: a chat turn triggered by the user's literal message is
``direct_user_request`` and its writes are ungated (the user's message IS the authorization —
the two-execution-paths invariant). Any other origin (autonomous scheduler/perception, a
headless lead, a custom agent) is gated-by-construction. Phase-2 per-argument provenance taint
is a separate later security plan. The source is a literal captured at the seam — NEVER
LLM-supplied.
"""

from __future__ import annotations

from typing import Final


class AuthorizationSource:
    DIRECT_USER_REQUEST: Final = "direct_user_request"
    AUTONOMOUS: Final = "autonomous"
    HEADLESS: Final = "headless"
    CUSTOM: Final = "custom"


def is_gated_source(source: str) -> bool:
    """True iff a write from this source must pass the approval gate. Fail-closed: only the
    exact ``direct_user_request`` literal is ungated; everything else (incl. unknown) is gated."""
    return source != AuthorizationSource.DIRECT_USER_REQUEST
```
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full gate + ruff.**
- [ ] **Step 6: Commit** `feat(rebuild): authorization_source phase-1 constants for the deep gate (Step 6B)`.

---

## Task 2: the `trust_gate` middleware (`wrap_tool_call` + `interrupt()`)

**Files:** Create `backend/src/deep_runtime/middleware/trust_gate.py` + `backend/tests/deep_runtime/test_trust_gate.py`.

**Design:** a factory `make_trust_gate_middleware(*, authorization_source, workspace_id, user_id, thread_id, db_factory, trust_engine, risk_assessor, create_approval)` returning a `@wrap_tool_call` middleware. Logic per call:
1. built-in name → `handler` (fall through). Reuse `DEEPAGENTS_BUILTIN_NAMES`.
2. `not is_gated_source(authorization_source)` → `handler` (ungated — the dormant chat path).
3. resolve `capability` via `ToolRegistry(db).get_tool(name).capability` (own lookup, mirrors `capability_scope._is_in_scope`); if capability is falsy or a read capability → `handler`.
4. gated write: `risk = await risk_assessor.get_or_assess_risk(capability, …)`; `decision = await trust_engine.evaluate(capability, risk, workspace_id)`; **IRREVERSIBLE override:** if `is_write_verification_required(capability, risk)` → treat as `approval_required`.
5. if `approval_required`: `approval = await create_approval(...)` with `artifact_refs={"thread_id": thread_id, "capability": capability, "reversible": risk.reversible, "blast_radius": risk.blast_radius, "tool_name": name}`, `status="pending"`; then `verdict = interrupt({"approval_id": approval.approval_id, "thread_id": thread_id, "capability": capability, "risk_level": risk.risk_level})`. On resume: if `verdict in ("approve", {"decision":"approve"})` → `handler`; else → `ToolMessage(content=json.dumps({"error":"rejected by approver","rejected":True}), tool_call_id=request.tool_call["id"], name=name, status="error")`.
6. else (`auto_execute_*`) → `handler`.

**Do NOT** wrap `interrupt()`/`handler` in a blanket `try/except` (GraphInterrupt must propagate).

- [ ] **Step 1: Write the failing tests** — drive the middleware directly (SimpleNamespace request + AsyncMock handler, mirroring `test_jarvis_tool_dispatcher.py`) for the non-interrupt branches, and a compiled-agent interrupt/resume test (mirror the Task-0 spike) for the pausing branch:
```python
"""Step 6B: trust_gate short-circuits direct_user_request, gates non-direct writes, applies the
IRREVERSIBLE override, and honors approve/reject verdicts."""

async def test_direct_user_request_short_circuits():
    # authorization_source=direct_user_request -> handler called, trust_engine/risk NEVER called.
    ...

async def test_builtin_falls_through():
    # write_todos -> handler, no capability lookup.
    ...

async def test_gated_read_capability_falls_through():
    # authorization_source=autonomous but capability is a read -> handler (no gate).
    ...

async def test_gated_irreversible_write_forces_interrupt_then_approve_executes():
    # autonomous + irreversible write: compiled agent pauses (interrupt); Approval persisted with
    # thread_id; Command(resume="approve") -> tool executes.
    ...

async def test_gated_write_reject_blocks():
    # Command(resume="reject") -> ToolMessage status=error, handler/execute never runs.
    ...

async def test_auto_execute_when_trusted_and_reversible():
    # autonomous + reversible + trust=autonomous + risk=low -> auto (handler), no interrupt.
    ...
```
(For the gated tests, stub `trust_engine.evaluate`/`risk_assessor.get_or_assess_risk` and the capability lookup like `test_deep_runtime_tool_execution.py` stubs `_is_in_scope`; use a fake `create_approval` recording the artifact_refs.)
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `trust_gate.py` per the design above (imports: `from langgraph.types import Command` is NOT needed here — the gate only CALLS `interrupt`; import `from langgraph.types import interrupt`; `from langchain.agents.middleware import AgentMiddleware, wrap_tool_call`; `from langchain_core.messages import ToolMessage`; `from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES`; `from src.deep_runtime.authorization import is_gated_source`; `from src.services.verification.predicate import is_write_verification_required`). Verify the exact `interrupt` import path against the Task-0 spike.
- [ ] **Step 4: Run → PASS.** Then `uv run pytest tests/deep_runtime/ -v`.
- [ ] **Step 5: Full gate + ruff.**
- [ ] **Step 6: Commit** `feat(rebuild): trust_gate middleware (wrap_tool_call + interrupt) for the deep runtime (Step 6B)`.

---

## Task 3: surface an `approval_needed` frame in the stream

**Files:** Modify `backend/src/deep_runtime/stream_adapter.py`; create `backend/tests/deep_runtime/test_stream_adapter_interrupt.py`.

> Scope lever B: only the backend-observable dict frame. The typed `ApprovalNeeded` CoreEvent + `core_events.py` HTTP-emission arms are DEFERRED to the frontend (D7) — do NOT touch `core_events.py` in 6B.

- [ ] **Step 1: Write the failing test.** `test_stream_adapter_interrupt.py`: build a compiled agent (Task-0/Task-2 style) whose gate interrupts; stream via `stream_deep_agent_events`; assert an `{"event":"approval_needed", "approval_id":…, "thread_id":…, "capability":…, "risk_level":…}` frame is emitted and NO `error` frame and NO `agent_done`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** In `stream_adapter.py`: `from langgraph.errors import GraphInterrupt`; add `except GraphInterrupt as gi:` BEFORE the generic `except Exception`, extracting the interrupt payload (`gi.args[0]` / `gi.interrupts[0].value` — confirm the exact shape from the Task-0 spike) and yielding `{"event":"approval_needed","agent":agent_name,"approval_id":…,"thread_id":config["configurable"]["thread_id"],"capability":…,"risk_level":…}` then `return`. If the Task-0 spike showed `__interrupt__` also arrives in the `updates` payload, handle it in the `updates` branch too (emit the frame, break/return). Do NOT add a typed CoreEvent (deferred, D7).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full gate + ruff.**
- [ ] **Step 6: Commit** `feat(rebuild): stream adapter emits approval_needed on GraphInterrupt (Step 6B)`.

---

## Task 4: `AgentInvoker.resume_deep_turn` (direct `Command(resume=…)`)

**Files:** Modify `backend/src/orchestrator/agent_invoker.py`; create `backend/tests/test_agent_invoker_resume.py`.

**Design:** `async def resume_deep_turn(self, *, approval_id, decision, user_id, workspace_id) -> AsyncGenerator[dict,…]`: load the `Approval`, read `artifact_refs["thread_id"]`, rebuild the deep agent identically to the seam (same shells + `trust_gate` + dispatcher + SystemMessage + the `app.state` durable checkpointer via the provider), then `astream(Command(resume=decision), {"configurable":{"thread_id": thread_id}}, durability="sync")` through `stream_deep_agent_events`, yielding frames. Mark the Approval `approved`/`rejected` before resuming. The agent to rebuild is recorded on the Approval (store `agent_name` in artifact_refs at interrupt time — add it in Task 2's `create_approval` call).

- [ ] **Step 1: Write the failing test** — `resume_deep_turn` reads thread_id from a fake Approval, rebuilds via patched `build_deep_agent`, and calls `astream(Command(resume="approve"), …)` (assert the Command + thread_id); patched `stream_deep_agent_events` yields an `agent_done`. Also assert the Approval status transitions to `approved`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Reuse the seam's build block (extract a private `_build_deep_agent_for(agent, tools, user_id, workspace_id, thread_id, authorization_source)` helper shared by `call_agent_stream` and `resume_deep_turn` — DRY the wiring so resume rebuilds identically). `import from langgraph.types import Command`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full gate + ruff.** Confirm `call_agent_stream`'s deep branch still behaves identically (the extracted helper is byte-equivalent).
- [ ] **Step 6: Commit** `feat(rebuild): AgentInvoker.resume_deep_turn re-enters a paused deep turn via Command(resume) (Step 6B)`.

---

## Task 5 (BLAST-RADIUS): wire the gate into the live seam

**Files:** Modify `backend/src/orchestrator/agent_invoker.py` (the `runtime=="deep"` branch); extend `backend/tests/test_agent_invoker_deep_hardening.py`.

> The one live-chat-entry task. Because the gate SHORT-CIRCUITS `direct_user_request`, live behavior must stay byte-identical. **2-stage PARALLEL review** (spec + quality) on the frozen commit. The resume HTTP endpoint is DEFERRED (scope lever A / D5) — this task touches ONLY the seam.

- [ ] **Step 1: Write the failing test** — extend `tests/test_agent_invoker_deep_hardening.py`-style: under `runtime="deep"`, assert the deep branch now passes `extra_middleware=(trust_gate_sentinel, dispatcher_sentinel)` (gate outer of dispatcher), builds the gate with `authorization_source="direct_user_request"` and the SAME `thread_id` used in `config`, and passes `durability="sync"` to the stream. Assert `runtime="legacy"` still routes to `agent_loop`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Deep branch only: mint `thread_id = generate_id("chat")` once; build `trust_gate = make_trust_gate_middleware(authorization_source=AuthorizationSource.DIRECT_USER_REQUEST, workspace_id=…, user_id=…, thread_id=thread_id, db_factory=self._db_factory, trust_engine=…, risk_assessor=…, create_approval=…)`; pass `extra_middleware=(trust_gate, dispatcher)`; pass `durability="sync"` down to `stream_deep_agent_events`'s `astream` (thread a param through the adapter, default None/omit to keep other callers unchanged). Reuse the shared `_build_deep_agent_for(...)` helper from Task 4 so the seam and `resume_deep_turn` build identically. Resolve `trust_engine`/`risk_assessor`/`create_approval` from the orchestrator's services (follow the existing DI pattern — verify how `AgentInvoker` reaches services). Do NOT add a routes_chat endpoint (deferred).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full gate + ruff + legacy/direct neutrality:** `uv run pytest tests/ -k "invoker or chat or runtime_branch or agent_loop" --ignore=tests/e2e -v`. Confirm a `direct_user_request` deep turn calls NO trust_engine/risk_assessor (dormant).
- [ ] **Step 6: Commit** `feat(rebuild): deep seam wires the trust gate, dormant on direct chat (Step 6B)`.

---

## Task 6: end-to-end forced-provenance gate guard

**Files:** Create `backend/tests/test_deep_gate_end_to_end.py`.

> The load-bearing guard. Prove the WHOLE machinery with `authorization_source="autonomous"` FORCED (does not occur on live chat), via a compiled `build_deep_agent` + `stream_deep_agent_events` + a fake model, no real API.

- [ ] **Step 1: Write the test.** Build the deep agent with `authorization_source="autonomous"`, a write capability whose trust/risk force `approval_required` (or an IRREVERSIBLE capability), a fake execute_tool stub, and a fake scripted model calling the write tool. Assert: (a) turn-1 stream emits `approval_needed` with an `approval_id`+`thread_id`, no `error`, no `agent_done`; (b) an `Approval` row exists with `artifact_refs["thread_id"]` == the frame's thread_id, `status="pending"`; (c) `resume_deep_turn(approval_id, "approve")` re-streams and the tool EXECUTES (`execute_tool` called, `tool_result` frame, `agent_done`); (d) a fresh run + `resume_deep_turn(approval_id2, "reject")` → tool does NOT execute, a rejection `tool_result`(blocked). Then a **control**: same wiring but `authorization_source="direct_user_request"` → NO `approval_needed`, tool executes immediately (ungated). A failure is a real integration gap — fix the code.
- [ ] **Step 2: Run → PASS.** Negative control: temporarily flip the gate to always short-circuit and confirm the forced-provenance assertions FAIL (then restore).
- [ ] **Step 3: Full gate + ruff. Commit** `test(rebuild): deep gate end-to-end (forced provenance) — interrupt, persist, resume, reject (Step 6B)`.

---

## Task 7: durable-saver live-proof (real DB, no API)

**Files:** Create `backend/tests/test_deep_gate_durable_resume_db.py` (real-DB, skip if Postgres unreachable — the self-contained `_db_reachable` idiom).

- [ ] Prove the interrupt→resume spans the DURABLE `AsyncPostgresSaver` (not just MemorySaver): build the gate agent with a real `build_async_postgres_saver` checkpointer + `authorization_source="autonomous"`; interrupt; then rebuild a NEW agent object over the SAME saver + thread_id and `Command(resume="approve")` → tool executes (proves the paused state was recovered from Postgres, i.e., survives object/process boundaries). Assert `alembic check` stays drift-free. Commit `test(rebuild): deep gate resumes across a fresh agent over the durable AsyncPostgresSaver (Step 6B)`.

---

## Task 9 (OPTIONAL, live): gate smoke behind the flag

**File:** `backend/spikes/deep_stream/live_gate_smoke.py` (create; not a pytest test).

- [ ] Only if `JARVIS_ANTHROPIC_API_KEY` is set. `JARVIS_RUNTIME=deep`; force `authorization_source="autonomous"` for one write; observe a real `approval_needed` + a real `Command(resume="approve")` executing the tool. Document; do NOT gate CI. Commit under `spikes/`.

---

## Task 10: docs note

**File:** `CLAUDE.md`

- [ ] Update the Step 6A.5 note area: add a Step 6B bullet — the deep runtime now has THE ONE GATE (a `trust_gate` `wrap_tool_call`+`interrupt()` middleware between `capability_scope` and the dispatcher) that **short-circuits `direct_user_request` (dormant on live chat = today's behavior)** and, for autonomous/headless/custom provenance, evaluates trust×risk + an IRREVERSIBLE hard override → pauses via `interrupt()`, persists an `Approval` (thread_id in `artifact_refs`), and resumes via `Command(resume=…)` through `AgentInvoker.resume_deep_turn`. `authorization_source` is a phase-1 literal. Note still-6C-owned: Redis write lock, kill-Operator, fast-path routing, user-approval trust relocation; and the resume HTTP endpoint + typed `approval_needed` CoreEvent + phase-2 per-arg provenance taint + the frontend chat-approval UX are later. Factual, no volatile counts. Commit `chore(rebuild): doc note — the one approval gate on the deep runtime, dormant on direct chat (Step 6B)`.

---

## Review strategy (for the executor)

- **Task 0 (spike)** — combined review re-running the spike; confirm the streaming interrupt + `Command(resume)` behavior is real and documented (it drives Tasks 2/3).
- **Tasks 1/2/3/4/6/7/10** — single combined review each (spec + quality). Task 2 (gate) and Task 6 (e2e guard) are the load-bearing ones: the reviewer independently confirms the direct-user short-circuit calls NO trust/risk, the IRREVERSIBLE override forces approval, and the reject path never executes the tool.
- **Task 5 (deep seam)** — the blast-radius task → **2-stage PARALLEL review** (spec + quality) on the frozen commit; the quality reviewer confirms a `direct_user_request` deep turn is byte-behavior-identical (gate dormant, no trust/risk calls, `astream` still streams the same 7 shapes) and `legacy` is unchanged, and that gate/dispatcher order is (gate outer, dispatcher inner) with capability_scope outermost.
- **Final holistic review (opus):** full gate green, `alembic check` drift-free (no migrations), `JARVIS_RUNTIME=legacy` AND `deep`+direct-chat both behavior-neutral, and the load-bearing guarantee — a FORCED-provenance gated write actually pauses (interrupt), persists an Approval with the thread_id, and `Command(resume)` executes on approve / blocks on reject through the durable saver (Task 6 + Task 7 are real guards: confirm they fail if the gate is bypassed). Confirm nothing 6C-owned (Redis lock, kill-Operator, fast-path, trust relocation) and no frontend leaked in, and no migration was added.

---

## Self-review checklist (run before dispatching implementers)

1. **Spec coverage:** gate policy D0 = T2 (short-circuit) + T6 (forced-provenance proof); wrap_tool_call+interrupt D1 = T0 (spike) + T2; IRREVERSIBLE override D2 = T2 + T6; authorization_source D3 = T1 + T5 (seam literal); stable thread_id D4 = T2 (persist) + T4 (recover) + T5 (mint); resume D5 = T4 (method) + T6/T7 (proof) — HTTP endpoint DEFERRED (lever A); durability=sync D6 = T5; approval_needed dict frame D7 = T3 — typed CoreEvent DEFERRED (lever B); no-migration/no-frontend/no-6C D8 = enforced throughout + review. ✅
2. **Placeholder scan:** the empirical unknowns — exactly how the interrupt surfaces in `astream` (raise vs `__interrupt__` update), the `interrupt` import path, the `GraphInterrupt` payload shape — are pinned by Task 0's spike BEFORE Tasks 2/3 build on them, not left as TODOs. ✅
3. **Type/name consistency:** `AuthorizationSource`, `is_gated_source`, `make_trust_gate_middleware`, `resume_deep_turn`, `approval_needed`, `artifact_refs["thread_id"]`, `Command(resume=…)`, `durability="sync"` used identically across tasks. Chain order capability_scope→trust_gate→dispatcher stated identically in D1/T5/review. ✅
4. **No migrations** → `alembic check` drift-free; thread_id rides `Approval.artifact_refs` JSONB. ✅
5. **Scope discipline:** the gate is DORMANT on direct chat (D0); 6C items (Redis lock, kill-Operator, fast-path, trust relocation) and the frontend approval UX + phase-2 taint are explicitly OUT; the resume HTTP endpoint (lever A) + the typed `ApprovalNeeded` CoreEvent (lever B) are DEFERRED to their frontend/6C consumers; the gate does NOT take the write lock; `call_agent` (perception) untouched. ✅
6. **Security:** `authorization_source` is a seam literal, never LLM-supplied; the gate is SEPARATE from dispatch and from capability_scope (defense-in-depth); IRREVERSIBLE override is fail-closed (unknown write ⇒ irreversible ⇒ approval); RiskAssessor stays fail-closed `high`. ✅
