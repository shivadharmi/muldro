# Step 7C — Inline Read-Back + Middleware Wiring + Deep Trust-Increment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Grounded by 4 parallel code-extraction passes +
> 4 user-resolved forks (2026-07-08). Every current-state claim is anchored `file:line`;
> re-verify at implementation — anchors rot.

**Goal:** Complete Step 7 (cognitive-agent collapse) by giving the deep-runtime chat lead the
last correctness machinery: an inline `ReadBackVerifier` post-execute middleware (inner of
write_lock) that verifies irreversible/external writes and annotates the verdict, escalate-first
compensation on a contradicted effect, a deep trust-increment-on-CONFIRMED (`begin_nested()`
SAVEPOINT from the start), and wiring the two written-but-unwired deep middlewares (`budget`,
`unavailable_server`). Read-back is DORMANT behind `deep_readback_enabled=False` (proven via
forced/offline tests); budget + unavailable_server are wired unconditionally (always-on hygiene).
Legacy path byte-identical throughout.

**Architecture:** 7C extends the deep `wrap_tool_call` onion assembled in
`AgentInvoker._build_deep_agent_for` (`src/orchestrator/agent_invoker.py:197-394`). All of
`src/services/verification/` (the path-agnostic Step-3 package) is REUSED verbatim — 7C adds only
a deep host. The read-back middleware mirrors the **deferred-tick** template (`read_fn=None` → every
irreversible write resolves to `UNVERIFIED`, never `CONTRADICTED`; escalation/increment proven via a
forced mock read_fn), NOT the inline autonomous template (which uses a real `run_readback` seam that
would false-CONTRADICT `calendar.create`). The trust-increment copies the deferred tick's DB-only
`record_approval_decision(...)` wrapped in `begin_nested()`, NOT the full `TrustGate`.

**Tech Stack:** Python 3.13; LangChain/LangGraph deep-agents 0.6.11 middleware
(`wrap_tool_call`/`after_model`); `src/services/verification/` (`ReadBackVerifier`/`VerifyVerdict`/
`predicate`/`post_conditions`/`compensation`); `record_approval_decision` (`src/services/risk_assessor.py`);
`MODEL_TIER_IDS` (`src/deep_runtime/model_factory.py`); pytest (custom asyncio hook, NO pytest-asyncio);
real Postgres+Redis+Qdrant for the DB tests.

---

## Baseline (verified at scoping, 2026-07-08)

- Branch `rebuild/first-principles` (off `main`, NOT pushed). HEAD `17c564f` (this plan's skeleton),
  parent `acb6058` (Step 7B2 P6).
- `uv run pytest tests/ --ignore=tests/e2e` → **3299 passed / 18 skipped** (18 = true green;
  a gate with ~108 skipped ⇒ redis/postgres DOWN ⇒ NOT green; `docker start hyperlocal-redis` serves :6379).
- `uv run alembic heads` → single `1a2770a28c39`; `alembic check` drift-free; `ruff check src tests` clean.
- INFRA: uv venv has NO pip → `uv sync --all-extras`; custom `pytest_pyfunc_call` asyncio hook;
  real-DB/Redis tests self-contained (`_db_reachable`/`_redis_reachable` + NullPool + User→Workspace FK
  seed + **UUID-suffixed Redis keys**). Do NOT edit `backend/` while a `uvicorn --reload` worker runs.
- All commands run from `backend/` via `uv run ...`.

## Forks (RESOLVED with user, 2026-07-08)

1. **Dormancy shape → DORMANT behind a flag.** New `deep_readback_enabled: bool = False`; read_fn=None
   (deferred-tick template). Deep chat byte-identical flag-off; proven via forced tests. Live
   activation (flag + real per-connector read_fn + gated producer) = Step 10.
2. **budget + unavailable_server → WIRE now, unconditionally.** Deps all exist; budget is additive
   (no double-count); unavailable_server self-contains its breaker (the 7A "re-auth breaker" worry is
   STALE). Cost: update 3 tuple-shape tests.
3. **Packaging → ONE combined plan.** All three pieces touch the same seam.
4. **Deferred-recheck loop → TRULY DEFERRED.** Deep has no `TaskStep`/`completed_unverified` surface
   (grep-confirmed); no deep equivalent in 7C.

## Extraction findings that shape this plan (all `file:line`, verify-don't-trust)

**Verification package (reused verbatim):**
- `ReadBackVerifier(read_fn: ReadFn | None)` where `ReadFn = Callable[[str, dict], Awaitable[object]]`
  (`readback.py:30,40`). `verify_step(*, capability, write_input, write_output, risk) -> VerifyVerdict`
  (`readback.py:43-45`), all keyword-only. `risk` is duck-typed (`.reversible`/`.blast_radius`/`.risk_level`
  — a `RiskAssessment` or `_Risk` shim satisfies it).
- Verdicts (`readback.py:33-36`): `CONFIRMED`/`CONTRADICTED`/`UNVERIFIED`. `read_fn=None` →
  irreversible-with-post-condition → **UNVERIFIED** (`readback.py:63-64`); a not-verification-required cap
  → **CONFIRMED trivially** (`readback.py:47-48`); a read error is UNVERIFIED, never CONTRADICTED
  (`readback.py:69-74`).
- `is_write_verification_required(capability, risk)` (`predicate.py:69-85`): `True` if statically
  irreversible OR (risk not None AND `IRREVERSIBLE(reversible, blast_radius)`); `risk=None` → static-only.
- `build_divergence_escalation(*, capability, artifact_ref, observed) -> dict` (`compensation.py:46-63`)
  → `{capability, artifact_ref, observed, compensator|None}`. NO auto-run of the compensator (escalate-first).
- **The `calendar.get`→`query_freebusy` denylist is `_READBACK_UNSERVABLE_CAPABILITIES` in
  `step_runner.py:38`, OUTSIDE the package** (memory's "denylisted in run_readback" was mis-located).
  `calendar.create` is the ONLY registered POST_CONDITION and is mock-only (`post_conditions.py:61-69`).
  → A real deep `read_fn` would false-CONTRADICT it; **read_fn=None avoids this entirely.**

**Deep chain + annotation (host):**
- Current `extra_middleware` (`agent_invoker.py:360-366`) flag-off:
  `(governor_audit, trust_gate, write_lock, dispatcher, librarian_extract)`; `capability_scope` prepended
  by `build_deep_agent`; `critique` prepended when `deep_delegates_enabled` (`:377-383`). Tuple order =
  outer→inner. `librarian_extract` + `budget` are `@after_model` (tuple position irrelevant).
- Read-back INNER of write_lock, OUTER of dispatcher ⇒ tuple `(..., write_lock, readback, dispatcher, ...)`.
- Reuse the memoized `_resolve_tool_def_shared` (`agent_invoker.py:233-240`) for capability, and the
  `_assess_risk` closure (`agent_invoker.py:251-271`, redis from `services.extras`, fail-closed-to-high)
  for `risk`.
- **Annotation pattern** = critique's `_annotate_content` (`governor_delegate_critique.py:74-92`): parse
  content-JSON, add a KEY (not `status` — `status` is binary; SSE maps `blocked ← status=="error"`,
  `stream_adapter.py:197`), `json.dumps(..., default=str)`. **NO stream_adapter change** (it passes
  tool_result content through unchanged, `stream_adapter.py:188-199`). The dispatcher returns a BARE
  `ToolMessage` (`jarvis_tool_dispatcher.py:71-76`) — do NOT copy critique's Command-unwrap (that's
  `task`-specific).
- **read_back runs EXACTLY ONCE post-approval** (inner of trust_gate's `interrupt()`; on reject the
  handler is never called) — no replay-idempotency defense needed (`trust_gate.py:27-33,313-386`).
- `is_gated_source(authorization_source)` (`authorization.py:23-26`): everything except
  `direct_user_request` is gated. Live chat seam passes `direct_user_request` (`agent_invoker.py:501`),
  resume passes `autonomous` (`:681`). **Trust-increment fires only for gated sources.**

**budget + unavailable_server (wire):**
- `make_budget_middleware(*, agent_name, model, workspace_id, db_factory, budget, trace_id=None, trigger="chat")`
  (`budget.py:38-47`), `@after_model`. Deps: `agent.name`, `self._budget` (`agent_invoker.py:113`),
  `self._db_factory`, `workspace_id` all in scope. **model MUST be `MODEL_TIER_IDS[agent.model_tier]`**
  (direct Anthropic id) NOT `get_model_for_agent` (Bedrock-tainted, `agent_invoker.py:139-143`). ADDITIVE —
  `stream_adapter.py:258` `calculate_cost` is pure (no `record_usage`); deep persists no `TokenUsage` today.
- `make_unavailable_server_middleware(*, workspace_id, db_factory, resolve_server=None)`
  (`unavailable_server.py:114-119`), `@wrap_tool_call`, self-contained per-turn breaker sets. Place OUTER
  of trust_gate (so a known-down WRITE tool is not prompted for approval).
- Both in `__all__` but NEITHER wired today. Neither reads a settings flag (no MagicMock-truthy hazard).
- **3 tuple-shape tests break and MUST be updated in the wiring commit:**
  `tests/test_agent_invoker_deep_hardening.py:143-152` (exact 5-tuple),
  `tests/deep_runtime/test_governor_delegate_critique.py:425-426` (critique `[0]`, `len==6`) + `:440` (`len==5`).

**Trust-increment (mirror):**
- Copy the deferred tick, NOT `TrustGate`: `record_approval_decision(db, workspace_id, capability, risk_level, decision_type)`
  (`risk_assessor.py:292+`, DB-only, `db.flush()`) wrapped in `async with db.begin_nested():` best-effort
  (`deferred_verification_tick.py:80-107`). The sibling autonomous SAVEPOINTs are verified:
  `record_auto_execution_outcome` (`trust_gate.py:239`, retrofitted 7A P0 `aca6e75`) +
  `record_user_approval_outcome` (`trust_gate.py:260`, born-with `276b493`).
- `decision_type` = **"approved"** on the deep path (the deep interrupt verdict is a bare `"approve"`;
  the modified/approved distinction is not captured on the deep gate — a Step-10 refinement). No Approval
  re-read needed.

## File Structure (locked)

- **Create:** `src/deep_runtime/middleware/readback.py` — `make_readback_middleware`.
- **Modify:** `src/config/settings.py` — add `deep_readback_enabled: bool = False`.
- **Modify:** `tests/conftest.py` — default `deep_readback_enabled=False` in `make_mock_settings`
  (the 7B2 MagicMock-truthy fix; CRITICAL).
- **Modify:** `src/orchestrator/agent_invoker.py` `_build_deep_agent_for` — build the `_record_confirmed_outcome`
  closure + the read_back middleware (flag-gated into the tuple, inner of write_lock); wire budget (`@after_model`,
  append) + unavailable_server (`@wrap_tool_call`, OUTER of trust_gate). **THE HOT SHARED FILE — single-owner +
  SYNCHRONOUS implementer dispatch; sequence its touches P1→P2→P3.**
- **Modify:** `src/deep_runtime/middleware/__init__.py` — add `make_readback_middleware` to imports + `__all__`.
- **Modify:** `tests/test_agent_invoker_deep_hardening.py` + `tests/deep_runtime/test_governor_delegate_critique.py`
  — update the 3 tuple-shape assertions (Phase 3).
- **Create:** `backend/spikes/deep_readback/probe.py` + `docs/superpowers/spikes/2026-07-08-deep-readback.md` (Phase 0).
- **Create:** `tests/deep_runtime/test_readback_middleware.py` (unit) + a forced-on offline e2e guard.
- **Reuse verbatim (NO change):** all of `src/services/verification/` + `middleware/{budget,unavailable_server}.py`.
- **Expect NO migration** (read-back is inline; trust-increment reuses existing tables). Head stays `1a2770a28c39`.

## Review strategy (locked, per 7A/7B1/7B2 rhythm)

- **Phase 5 seam (`agent_invoker._build_deep_agent_for`) = 2-stage PARALLEL spec+quality review on the frozen
  commit** (quality proves LEGACY byte-identical + flag-off deep = the pre-read_back tuple + budget/unavail;
  spec proves the read-back logic/placement match the plan).
- **Load-bearing read-back + trust-increment = independent opus review.**
- **Final holistic opus** re-runs the full gate + alembic AND INDEPENDENTLY reproduces EVERY negative control.
- Single-owner-per-file + **SYNCHRONOUS implementer dispatch** (`run_in_background:false`) — `agent_invoker.py`
  is HOT (touched P1→P2→P3); sequence, never concurrent (the 6B duplicate-def lesson).
- **FULL non-e2e gate at EVERY checkpoint** (18 skipped, not ~108 — subset-green ≠ gate-green, the 7B1 lesson).
- Every load-bearing guard has a negative control WITH TEETH (revert-fix → test fails → `git checkout` restore).

## Guardrails (carried lessons)

- VERIFY-DON'T-TRUST every current-state claim at `file:line` — spec/memory/CLAUDE.md/extraction all rot.
- STAY DORMANT: read_back flag-gated; deep byte-neutral flag-off (modulo the unconditional budget/unavailable
  hygiene, which is a deliberate always-on deep addition — legacy is untouched); dormant-but-PROVEN.
- Any new cheap-model/redis cache sources redis from `self._services.extras.get("redis")` (the 6C bug) —
  N/A here (read_back has no redis dep; `_assess_risk` already sources it correctly).
- The trust-increment needs `begin_nested()` SAVEPOINT from the START (revert-and-rerun-prove load-bearing).
- Do NOT edit CLAUDE.md for dormant deep internals (durable only at MERGE).
- If a task seems to need a migration, STOP and re-check the modeling (7C is inline, not persisted).

---

## Phase 0 — Offline spike (could DISPROVE the annotation path)

**Files:**
- Create: `backend/spikes/deep_readback/probe.py`
- Create: `docs/superpowers/spikes/2026-07-08-deep-readback.md`

**Why a spike:** the annotation-through-SSE mechanism is proven for critique's `task` Command
(`governor_delegate_critique.py`) and inner-of-write_lock placement is proven by `write_lock.py`, but the
SPECIFIC combination — a read-back `@wrap_tool_call` inner of write_lock reading a **bare** dispatcher
`ToolMessage`, re-annotating a content-JSON key, and that annotation surviving write_lock's `async with` +
the full chain + `stream_deep_agent_events` to a `tool_result` frame (blocked=false) — is unproven end-to-end
offline. Every prior deep phase's spike caught a non-obvious surprise; this one could DISPROVE the "no
stream_adapter change" claim or reveal a `model_copy`/content-type gotcha. Offline (fake scripted-streaming
model, no API key), mirroring `spikes/deep_delegate/*`.

- [ ] **Step 1: Write the probe.** Drive a REAL `build_deep_agent` (via a minimal harness mirroring
  `spikes/deep_delegate/subagent_gated_probe.py`) with a fake `BaseChatModel` that emits one tool call to a
  stub Jarvis tool, a `jarvis_tool_dispatcher` whose `execute_tool` returns a JSON `ToolMessage`, a
  `write_lock` (redis=None → passthrough), and a read-back-shaped middleware inner of write_lock that
  annotates `{"verification": {"verdict": "unverified"}}` onto the bare `ToolMessage.content`. Stream via
  `stream_deep_agent_events` and collect frames.

```python
# backend/spikes/deep_readback/probe.py  (sketch — fill against the delegate probe's harness)
import asyncio, json
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
# ... reuse the delegate probe's fake-model + build_deep_agent + stream harness ...

@wrap_tool_call
async def readback_probe(request, handler):
    result = await handler(request)
    if not isinstance(result, ToolMessage) or result.status == "error":
        return result
    obj = json.loads(result.content) if isinstance(result.content, str) else result.content
    if not isinstance(obj, dict):
        obj = {"result": obj}
    obj["verification"] = {"verdict": "unverified"}
    return result.model_copy(update={"content": json.dumps(obj, default=str)})

async def main():
    # build_deep_agent(..., extra_middleware=(write_lock, readback_probe, dispatcher))
    # frames = [f async for f in stream_deep_agent_events(agent, ...)]
    # assert a tool_result frame's "result" JSON contains verification.verdict == "unverified"
    # assert that frame's "blocked" is False
    # (SECOND probe) a mock read_fn CONTRADICTED path annotates verification.escalation and
    #   the frame is still blocked=False (escalate-first does not block a done write)
    ...

asyncio.run(main())
```

- [ ] **Step 2: Run it.** `uv run python spikes/deep_readback/probe.py`
  Expected: a `tool_result` frame whose `result` JSON carries `verification.verdict == "unverified"`,
  `blocked == False`; the CONTRADICTED sub-probe carries `verification.escalation` and stays `blocked == False`.
  If the annotation is DROPPED or the frame flips to blocked → the design is DISPROVEN; STOP and redesign
  (e.g. status handling, list-content). Document the outcome either way.

- [ ] **Step 3: Write the decision doc** `docs/superpowers/spikes/2026-07-08-deep-readback.md` — the empirical
  finding (annotation survives / no adapter change / bare-ToolMessage path, or the surprise + mitigation).

- [ ] **Step 4: Commit.**
```bash
git add backend/spikes/deep_readback/probe.py docs/superpowers/spikes/2026-07-08-deep-readback.md
git commit -m "spike(rebuild): deep read-back annotation survives SSE tool_result (Step 7C P0)"
```

---

## Phase 1 — The read-back middleware (dormant, flag-gated)

**Files:**
- Create: `src/deep_runtime/middleware/readback.py`
- Modify: `src/config/settings.py` (add `deep_readback_enabled`)
- Modify: `tests/conftest.py` (`make_mock_settings` default `deep_readback_enabled=False`)
- Modify: `src/deep_runtime/middleware/__init__.py` (`__all__`)
- Test: `tests/deep_runtime/test_readback_middleware.py`

### Task 1.1 — the settings flag + conftest default (do FIRST — the MagicMock-truthy fix)

- [ ] **Step 1: Add the flag.** In `src/config/settings.py`, next to `deep_delegates_enabled`
  (`settings.py:186`):
```python
    deep_readback_enabled: bool = False  # JARVIS_DEEP_READBACK_ENABLED
```

- [ ] **Step 2: Default it in the mock settings (CRITICAL).** In `tests/conftest.py` `make_mock_settings`,
  alongside the existing `deep_delegates_enabled=False` / `deep_inline_format=False` defaults:
```python
    settings.deep_readback_enabled = False
```
Rationale: `make_mock_settings` returns a MagicMock whose UNSET bool attrs are TRUTHY. Without this, every
`runtime="deep"` test would wire the read-back path. (The 7B2 P4 hazard — verified pattern.)

- [ ] **Step 3: Run the deep suite to confirm no accidental activation.**
  `uv run pytest tests/deep_runtime/ tests/test_agent_invoker_deep_hardening.py -q`
  Expected: PASS (flag default False → chain unchanged so far).

- [ ] **Step 4: Commit.**
```bash
git add src/config/settings.py tests/conftest.py
git commit -m "feat(rebuild): deep_readback_enabled flag (default off) + mock-settings default (Step 7C P1.1)"
```

### Task 1.2 — the middleware factory (verdict + annotate + escalate-first)

- [ ] **Step 1: Write the failing unit test.** `tests/deep_runtime/test_readback_middleware.py` — drive the
  middleware directly with a fake `handler` returning a JSON `ToolMessage` and a mock `resolve_capability`/
  `assess_risk`/`read_fn`. (Uses the `@wrap_tool_call`-invocation harness the other middleware tests use —
  mirror `tests/deep_runtime/test_write_lock.py` for how to call a `wrap_tool_call` middleware in isolation.)

```python
import json
import pytest
from langchain_core.messages import ToolMessage
from src.deep_runtime.middleware.readback import make_readback_middleware
from src.services.verification.readback import VerifyVerdict


def _tool_msg(payload: dict, *, status="success", tid="tc_1", name="send_email"):
    return ToolMessage(content=json.dumps(payload), tool_call_id=tid, name=name, status=status)


async def _run(mw, *, name="send_email", args=None, result):
    # Mirror the write_lock test harness: build a fake ToolCallRequest + handler.
    from tests.deep_runtime._mw_harness import invoke_wrap_tool_call  # or inline the helper used elsewhere
    return await invoke_wrap_tool_call(mw, name=name, args=args or {}, handler_result=result)


async def test_unverified_annotates_and_does_not_block():
    mw = make_readback_middleware(
        workspace_id="ws_1",
        authorization_source="direct_user_request",
        resolve_capability=lambda n: _async("email.send"),
        assess_risk=lambda cap, inp: _async(_Risk(reversible=False, blast_radius="external_single")),
        read_fn=None,  # deferred-tick template → irreversible → UNVERIFIED
    )
    out = await _run(mw, result=_tool_msg({"message_id": "m1"}))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == VerifyVerdict.UNVERIFIED.value
    assert out.status != "error"  # annotate, never block a completed write
```
(Provide `_async`/`_Risk` helpers in the test module: `_Risk` is a tiny object exposing
`.reversible`/`.blast_radius`/`.risk_level`; `_async(x)` wraps a value in an already-completed coroutine.
If a shared `wrap_tool_call` invocation helper does not exist, inline the minimal `ToolCallRequest`
construction the existing middleware tests use — check `tests/deep_runtime/test_write_lock.py` and copy it.)

- [ ] **Step 2: Run it — expect ImportError/fail.**
  `uv run pytest tests/deep_runtime/test_readback_middleware.py::test_unverified_annotates_and_does_not_block -q`
  Expected: FAIL (`make_readback_middleware` not defined).

- [ ] **Step 3: Implement `src/deep_runtime/middleware/readback.py`.**
```python
"""Deep-runtime inline read-back verifier middleware (Step 7C, spec §4.5).

Placed INNER of write_lock, OUTER of dispatcher:
  capability_scope → governor_audit → unavailable_server → trust_gate → write_lock → readback → dispatcher
So it runs the write via handler(request) (the dispatcher executes it), then — for an
irreversible/external write — reads the effect back and ANNOTATES the verdict onto the
ToolMessage content (a content-JSON key, NEVER `status`, so the SSE frame does not flip to
blocked). CONTRADICTED → an escalate-first divergence payload (the compensator is offered, never
auto-run). CONFIRMED + a gated authorization_source → the deep trust-increment (injected).

Reuses src.services.verification verbatim. `read_fn` defaults to None (the deferred-tick template:
every irreversible write with a post-condition resolves to UNVERIFIED — never CONTRADICTED — until a
live per-connector read seam lands; a real read_fn would false-CONTRADICT the mock-only
calendar.create). Tests inject a mock read_fn + post-condition to exercise CONFIRMED/CONTRADICTED.

DORMANT: added to the chain only when settings.deep_readback_enabled (default False).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.authorization import is_gated_source
from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.capabilities import is_read_only_capability
from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import ReadBackVerifier, ReadFn, VerifyVerdict

logger = logging.getLogger(__name__)

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]
AssessRiskFn = Callable[[str, dict], Awaitable[Any]]
RecordConfirmedFn = Callable[..., Awaitable[None]]


def _annotate(content: Any, verification: dict) -> str:
    """Add a `verification` content-JSON key (never touch status). default=str so a
    non-serializable content never raises (the critique middleware's discipline)."""
    try:
        obj = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, ValueError):
        obj = {"result": content}
    if not isinstance(obj, dict):
        obj = {"result": obj}
    obj["verification"] = verification
    return json.dumps(obj, default=str)


def make_readback_middleware(
    *,
    workspace_id: str,
    authorization_source: str,
    resolve_capability: ResolveCapabilityFn,
    assess_risk: AssessRiskFn,
    read_fn: ReadFn | None = None,
    record_confirmed_outcome: RecordConfirmedFn | None = None,
) -> AgentMiddleware:
    """Build the per-turn read-back middleware. `resolve_capability(name)->capability|None` and
    `assess_risk(capability, args)->risk` reuse the shared per-turn closures. `read_fn` is the
    injected verification seam (None on the dormant path). `record_confirmed_outcome(*, capability,
    risk_level)` fires the trust-increment only for gated writes (None = no-op)."""
    verifier = ReadBackVerifier(read_fn)

    @wrap_tool_call
    async def readback(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        result = await handler(request)
        # A blocked/contended/failed write (trust_gate reject, write_lock contention, dispatcher
        # error) carries status=="error" — nothing to verify, pass through unchanged.
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result

        capability = await resolve_capability(name)
        if not capability or is_read_only_capability(capability):
            return result  # reads are never read-back-verified

        risk = await assess_risk(capability, request.tool_call.get("args") or {})
        write_input = request.tool_call.get("args") or {}
        try:
            parsed = json.loads(result.content) if isinstance(result.content, str) else result.content
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        write_output = parsed if isinstance(parsed, dict) else {}

        verdict = await verifier.verify_step(
            capability=capability, write_input=write_input, write_output=write_output, risk=risk
        )
        verification: dict[str, Any] = {"verdict": verdict.value}

        if verdict == VerifyVerdict.CONTRADICTED:
            verification["escalation"] = build_divergence_escalation(
                capability=capability,
                artifact_ref=write_output or {},
                observed="read-back could not confirm the effect",
            )
            logger.warning(
                "[deep_runtime] read-back CONTRADICTED for %s (%s) — escalate-first", name, capability
            )
        elif verdict == VerifyVerdict.CONFIRMED and is_gated_source(authorization_source):
            if record_confirmed_outcome is not None:
                await record_confirmed_outcome(
                    capability=capability, risk_level=getattr(risk, "risk_level", "high")
                )

        return result.model_copy(update={"content": _annotate(result.content, verification)})

    return readback
```

- [ ] **Step 4: Run the test — expect PASS.**
  `uv run pytest tests/deep_runtime/test_readback_middleware.py::test_unverified_annotates_and_does_not_block -q`

- [ ] **Step 5: Add the remaining unit tests** (all against the factory directly):
  - `test_builtin_falls_through` — `write_todos` → handler result returned unchanged (no `verification` key).
  - `test_error_result_passthrough` — a `status="error"` handler result is returned unchanged.
  - `test_read_only_capability_skipped` — `resolve_capability→"email.read"` → no verification key.
  - `test_reversible_internal_confirmed_trivially` — `email.draft` (reversible-internal) + read_fn=None →
    `verdict=="confirmed"` and NO read_fn call (assert the mock read_fn was not awaited).
  - `test_contradicted_annotates_escalation_not_blocked` — a MOCK read_fn + a monkeypatched
    `POST_CONDITIONS["mock.write"]` whose assertion returns False → `verdict=="contradicted"`,
    `verification["escalation"]["capability"]=="mock.write"`, `out.status != "error"`.
  - `test_confirmed_gated_fires_increment` — MOCK read_fn asserting True + `authorization_source="autonomous"`
    → `record_confirmed_outcome` awaited once with `capability`/`risk_level`.
  - `test_confirmed_direct_chat_does_NOT_increment` — same but `authorization_source="direct_user_request"`
    → `record_confirmed_outcome` NOT awaited (the `is_gated_source` guard — a negative-control-with-teeth).

- [ ] **Step 6: Run the file — expect all PASS.**
  `uv run pytest tests/deep_runtime/test_readback_middleware.py -q`

- [ ] **Step 7: Export it.** In `src/deep_runtime/middleware/__init__.py` add the import + `__all__` entry
  `"make_readback_middleware"` (alphabetical/next to the others).

- [ ] **Step 8: Full gate.** `uv run pytest tests/ --ignore=tests/e2e -q` → expect 3299 + new tests, 18 skipped.
  `ruff check src tests`.

- [ ] **Step 9: Commit.**
```bash
git add src/deep_runtime/middleware/readback.py src/deep_runtime/middleware/__init__.py tests/deep_runtime/test_readback_middleware.py
git commit -m "feat(rebuild): deep inline read-back middleware (verdict+annotate+escalate-first), dormant (Step 7C P1.2)"
```

---

## Phase 2 — Deep trust-increment-on-CONFIRMED (SAVEPOINT from the start)

**Files:**
- Modify: `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for` — add the `_record_confirmed_outcome` closure)
- Test: `tests/deep_runtime/test_readback_increment.py` (real Postgres)

### Task 2.1 — the increment closure (mirrors the deferred tick)

- [ ] **Step 1: Write the failing real-DB test.** `tests/deep_runtime/test_readback_increment.py` — self-contained
  (`_db_reachable` skipif + own NullPool engine + User→Workspace seed). Build a `_record_confirmed_outcome`-shaped
  closure over the test session-factory and assert a CONFIRMED gated write increments the TrustState
  `approved_count`, and that a poison-in-`record_approval_decision` does NOT leave the session unusable (the
  SAVEPOINT). (Structure mirrors `tests/test_trust_feedback.py` + the Phase-1 CF-1 deferred-tick poison test.)

```python
import pytest
from src.services.risk_assessor import record_approval_decision
# self-contained env: engine (NullPool), seed User+Workspace, session factory `db_factory`

async def test_confirmed_increment_persists(db_factory, ws_id):
    async def _record_confirmed_outcome(*, capability, risk_level):
        async with db_factory() as db:
            try:
                async with db.begin_nested():
                    await record_approval_decision(db, ws_id, capability, risk_level, "approved")
            except Exception:
                pass
            await db.commit()
    await _record_confirmed_outcome(capability="email.send", risk_level="high")
    # assert TrustState(ws_id, "email.send").approved_count == 1

async def test_savepoint_isolates_a_poisoned_increment(db_factory, ws_id, monkeypatch):
    # monkeypatch record_approval_decision to flush a poison (SELECT 1/0) then raise.
    # WITH begin_nested + try/except: the closure's db.commit() succeeds (increment skipped, session clean).
    # (negative control in Phase 4 removes begin_nested → db.commit() raises PendingRollbackError.)
    ...
```

- [ ] **Step 2: Run — expect fail** (closure not yet in production code; the test exercises the pattern inline,
  so this step proves the pattern before wiring it). Expected: PASS for the persist test once the helper shape
  is right; the poison test is the load-bearing assertion.

- [ ] **Step 3: Add the closure to `_build_deep_agent_for`.** In `src/orchestrator/agent_invoker.py`, near
  `_assess_risk` (`:251`), add (VERIFY `record_approval_decision`'s exact signature at `risk_assessor.py:292`
  before writing — the extraction reported `(db, workspace_id, capability, risk_level, decision_type)`):
```python
        async def _record_confirmed_outcome(*, capability, risk_level):
            """Deep trust-increment on a CONFIRMED gated write. Copies the deferred tick
            (deferred_verification_tick.py:80-107): the DB-only record_approval_decision wrapped
            in a begin_nested() SAVEPOINT (the 6C #4 / 7A-P0 session-poisoning lesson — from the
            START), best-effort. decision_type="approved": the deep interrupt verdict is a bare
            "approve"; the modified/approved distinction is a Step-10 refinement. Fresh session per
            increment (dedicated), so the SAVEPOINT also guards a record_approval_decision mid-flush
            failure from aborting this session before its commit."""
            from src.services.risk_assessor import record_approval_decision

            try:
                async with self._db_factory() as db:
                    try:
                        async with db.begin_nested():
                            await record_approval_decision(
                                db, workspace_id, capability, risk_level, "approved"
                            )
                    except Exception:
                        logger.debug(
                            "[deep_runtime] trust-increment savepoint rolled back", exc_info=True
                        )
                    await db.commit()
            except Exception:
                logger.debug("[deep_runtime] deep trust-increment best-effort failed", exc_info=True)
```
(Do NOT pass it into the read-back middleware yet — that wiring is Phase 3; Phase 2 only defines + tests the
closure shape. Alternatively define it and wire in the same Phase-3 commit; keep the test here.)

- [ ] **Step 4: Run the file — expect PASS.** `uv run pytest tests/deep_runtime/test_readback_increment.py -q`
  (real Postgres; skips cleanly if DB down — but for a green checkpoint the DB MUST be up).

- [ ] **Step 5: Full gate + ruff.** `uv run pytest tests/ --ignore=tests/e2e -q`; `ruff check src tests`.

- [ ] **Step 6: Commit.**
```bash
git add src/orchestrator/agent_invoker.py tests/deep_runtime/test_readback_increment.py
git commit -m "feat(rebuild): deep trust-increment-on-CONFIRMED closure with begin_nested SAVEPOINT (Step 7C P2)"
```

---

## Phase 3 — Wire read_back (flag-gated) + budget + unavailable_server into the chain

**Files:**
- Modify: `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for` — the chain assembly)
- Modify: `tests/test_agent_invoker_deep_hardening.py` (tuple-shape assertion)
- Modify: `tests/deep_runtime/test_governor_delegate_critique.py` (2 tuple-shape assertions)

### Task 3.1 — assemble the new chain

- [ ] **Step 1: Import the three factories** at the top of `agent_invoker.py` (near `:28-35`):
```python
from src.deep_runtime.middleware.budget import make_budget_middleware
from src.deep_runtime.middleware.readback import make_readback_middleware
from src.deep_runtime.middleware.unavailable_server import make_unavailable_server_middleware
from src.deep_runtime.model_factory import MODEL_TIER_IDS   # VERIFY the import path/name
```

- [ ] **Step 2: Build unavailable_server + budget + read_back** in `_build_deep_agent_for`, after the existing
  `librarian_extract` block (`:346-351`) and BEFORE the `extra_middleware` tuple (`:360`):
```python
        # Step 7C: MCP-server-down breaker (per-turn, self-contained). Placed OUTER of trust_gate so
        # a known-down WRITE tool is short-circuited before it is prompted for approval.
        unavailable_server = make_unavailable_server_middleware(
            workspace_id=workspace_id,
            db_factory=self._db_factory,
        )

        # Step 7C: re-home the legacy agent_loop authoritative cost record (@after_model). ADDITIVE —
        # the deep path recorded no TokenUsage before. model MUST be the direct Anthropic id
        # (MODEL_TIER_IDS), NOT get_model_for_agent (Bedrock-tainted).
        budget = make_budget_middleware(
            agent_name=agent.name,
            model=MODEL_TIER_IDS[agent.model_tier],   # VERIFY attr name (agent.model_tier vs agent.tier)
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            budget=self._budget,
            trace_id=None,      # threading the real trace_id is a minor follow-up
            trigger="chat",
        )

        # Step 7C: inline read-back (DORMANT behind deep_readback_enabled). read_fn=None (deferred-tick
        # template). resolve_capability reuses the memoized shared resolver (fail-open cap|None, same as
        # write_lock); risk from the shared _assess_risk closure; the increment fires only for gated writes.
        async def _readback_cap(name: str):
            ok, td = await _resolve_tool_def_shared(name)
            return getattr(td, "capability", None) if (ok and td) else None

        read_back = make_readback_middleware(
            workspace_id=workspace_id,
            authorization_source=authorization_source,
            resolve_capability=_readback_cap,
            assess_risk=_assess_risk,
            read_fn=None,
            record_confirmed_outcome=_record_confirmed_outcome,
        )
```

- [ ] **Step 3: Rebuild the `extra_middleware` tuple.** Replace `:360-366`:
```python
        # Order (outer→inner). capability_scope is installed FIRST by build_deep_agent, so the full
        # tool chain is:
        #   capability_scope → governor_audit → unavailable_server → trust_gate → write_lock
        #     [→ readback (only when deep_readback_enabled)] → dispatcher
        # librarian_extract + budget are @after_model (position in the tuple is irrelevant).
        gated_tools: tuple[Any, ...] = (write_lock, dispatcher)
        if self._settings.deep_readback_enabled:
            gated_tools = (write_lock, read_back, dispatcher)

        extra_middleware: tuple[Any, ...] = (
            governor_audit,
            unavailable_server,
            trust_gate,
            *gated_tools,
            librarian_extract,
            budget,
        )
```
Keep the `deep_delegates_enabled` critique-prepend block (`:377-383`) unchanged — it prepends `critique`
as element 0.

- [ ] **Step 4: Run the deep suite — expect the 3 tuple-shape tests to FAIL** (shape changed intentionally).
  `uv run pytest tests/deep_runtime/ tests/test_agent_invoker_deep_hardening.py -q`

- [ ] **Step 5: Update the 3 tuple-shape assertions:**
  - `tests/test_agent_invoker_deep_hardening.py:143-152` — new flag-off (both dormant flags off) tuple is
    `(governor_audit, unavailable_server, trust_gate, write_lock, dispatcher, librarian_extract, budget)`
    (7 elements). Assert identity/order accordingly (name the middleware objects the test already captures;
    add `unavailable_server` at index 1 and `budget` last). **Add a flag-ON assertion**: with
    `deep_readback_enabled=True`, `read_back` appears between `write_lock` and `dispatcher` (index 4), length 8.
  - `tests/deep_runtime/test_governor_delegate_critique.py:425-426` — flag-on delegates: `critique` still `[0]`;
    length is now 8 (critique + the 7-tuple). Update `len == 6` → `len == 8`.
  - `:440` — flag-off delegates: `len == 5` → `len == 7`.

- [ ] **Step 6: Run the deep suite — expect PASS.**
  `uv run pytest tests/deep_runtime/ tests/test_agent_invoker_deep_hardening.py -q`

- [ ] **Step 7: FULL gate — watch for unavailable_server regressions.**
  `uv run pytest tests/ --ignore=tests/e2e -q`
  **WATCH-POINT:** wiring `unavailable_server` adds a per-tool-call server resolution
  (`async with db_factory() as db: ToolRegistry(...).get_tool(name)`) to EVERY deep tool call. A deep test
  that executes a tool with a non-functional (raising) mock `db_factory` may now break (the middleware returns
  None server when `db_factory is None`, but a RAISING mock propagates). If any deep tool-execution test
  fails, fix it (give it a working test `db_factory`, or confirm the failure is a genuine gap). Do NOT relax
  the middleware. Expect 18 skipped (not ~108).
  `ruff check src tests`.

- [ ] **Step 8: Confirm no migration + single head.**
  `uv run alembic heads` → `1a2770a28c39`; `uv run alembic check` → drift-free.

- [ ] **Step 9: Commit.**
```bash
git add src/orchestrator/agent_invoker.py tests/test_agent_invoker_deep_hardening.py tests/deep_runtime/test_governor_delegate_critique.py
git commit -m "feat(rebuild): wire read_back (dormant) + budget + unavailable_server into the deep chain (Step 7C P3)"
```

---

## Phase 4 — Forced-on offline e2e guard + negative controls WITH TEETH

**Files:**
- Test: `tests/deep_runtime/test_readback_e2e.py`

Drives the REAL `_build_deep_agent_for` → `build_deep_agent` → `stream_deep_agent_events` with
`deep_readback_enabled=True`, a fake scripted-streaming model, and a fake tool (mirror
`tests/deep_runtime/test_delegate_e2e.py`'s harness).

- [ ] **Step 1: Positive assertions (forced-on):**
  - An irreversible write (e.g. capability `email.send`, read_fn=None as wired) → the `tool_result` frame's
    `result` JSON carries `verification.verdict == "unverified"`, and the frame is NOT blocked (`blocked==False`).
  - The SSE contract is otherwise intact (the frozen frame set — `agent_done` etc. — survives; mirror the
    delegate-e2e frame assertions).
  - A `budget` `TokenUsage` row is persisted for the turn (real Postgres query for the workspace).
  - `unavailable_server` short-circuits a tool whose result carries an `auth_required` envelope (a later
    same-turn call to that server returns the cached auth_required + steer without executing) — mirror the
    unit test in `tests/deep_runtime/test_unavailable_server.py` but through the real chain.

- [ ] **Step 2: Negative controls (each: revert-fix → test FAILS → restore):**
  - **NC-A (flag gate):** with `deep_readback_enabled=False` → the `tool_result` frame has NO `verification`
    key (read_back not in the chain). Removing the `if self._settings.deep_readback_enabled` gate → the
    key appears with flag off → FAIL.
  - **NC-B (gated guard):** a forced `direct_user_request` CONFIRMED write does NOT increment trust; removing
    the `is_gated_source` guard → it increments → FAIL. (Unit-level in P1.2 test_7; assert here too if the
    e2e can reach a CONFIRMED via a reversible-internal gated write.)
  - **NC-C (SAVEPOINT):** removing `begin_nested()` from `_record_confirmed_outcome` → the poison test
    (P2 `test_savepoint_isolates_a_poisoned_increment`) → `db.commit()` raises `PendingRollbackError` → FAIL.
  - **NC-D (escalate-first):** neutering `build_divergence_escalation` (return `{}`) → the CONTRADICTED unit
    test (P1.2 test_contradicted) loses `verification.escalation` → FAIL.

- [ ] **Step 3: Run the file.** `uv run pytest tests/deep_runtime/test_readback_e2e.py -q` → PASS.

- [ ] **Step 4: Full gate + ruff.** `uv run pytest tests/ --ignore=tests/e2e -q`; `ruff check src tests`.

- [ ] **Step 5: Commit.**
```bash
git add tests/deep_runtime/test_readback_e2e.py
git commit -m "test(rebuild): forced-on deep read-back e2e guard + 4 negative controls (Step 7C P4)"
```

---

## Phase 5 — Holistic review + final gate

- [ ] **Step 1: Full gate one more time.** `uv run pytest tests/ --ignore=tests/e2e -q` → 3299 + new tests,
  **18 skipped** (NOT ~108); `ruff check src tests`; `uv run alembic heads` = `1a2770a28c39`; `uv run alembic check`
  drift-free.

- [ ] **Step 2: Independent holistic opus review** — re-runs the gate + alembic AND INDEPENDENTLY reproduces
  every negative control (NC-A..NC-D: revert-fix → fail → `git checkout` restore, tree clean). Confirms:
  legacy `agent_loop` branch + `call_agent` UNCHANGED (byte-identical); flag-off deep = the pre-read_back
  chain + the unconditional budget/unavailable hygiene; read_back dormant-but-PROVEN; NO CLAUDE.md edit; NO
  migration.

- [ ] **Step 3: NO CLAUDE.md edit** (dormant deep internals are not durable arch facts until MERGE — doc policy).

- [ ] **Step 4: Update memory** (`project_first_principles_rebuild.md` + `MEMORY.md`) with the STEP 7C DONE block:
  commits, what shipped, dormancy, carried Step-10 activation gates (real per-connector read_fn seam;
  decision_type modified/approved distinction on the deep gate; flip `deep_readback_enabled`; live gated
  producer), verify-don't-trust catches. Mark Step 7 COMPLETE (7A+7B1+7B2+7C).

## Carried Step-10 activation gates (documented, NOT built in 7C)

- Flip `deep_readback_enabled=True` + wire a REAL per-connector `read_fn` (reusing/mirroring
  `step_runner.run_readback`'s `_READBACK_UNSERVABLE_CAPABILITIES` denylist so `calendar.create` cannot
  false-CONTRADICT) — Step-3 carry-forward #1.
- A live GATED producer on the deep path (single-lead routing) so the trust-increment actually fires.
- decision_type modified/approved distinction on the deep gate (7C records "approved").
- The durable deferred-recheck loop for deep (needs a `completed_unverified` persistence surface deep lacks).
- Interactive compensator EXECUTION + `verification_divergence` UI (Step-3 CF #4).
- Carried 7B2 activation gates (unchanged): delegate error-path hardening, critique prompt-injection hardening,
  GP-disable process-global re-audit, 6C #2/#3.

## Self-review (writing-plans checklist)

- **Spec coverage:** read-back inline middleware ✅ (P1); escalate-first ✅ (P1, `build_divergence_escalation`);
  wire budget + unavailable_server ✅ (P3); deep trust-increment with SAVEPOINT-from-start ✅ (P2);
  deferred-recheck out of scope ✅ (Fork 4); no live activation ✅ (dormant flag).
- **No placeholders:** every code step shows the actual middleware/closure/wiring; test steps show real assertions.
- **Type consistency:** `make_readback_middleware(*, workspace_id, authorization_source, resolve_capability,
  assess_risk, read_fn=None, record_confirmed_outcome=None)`; `_record_confirmed_outcome(*, capability, risk_level)`;
  `verify_step(*, capability, write_input, write_output, risk)`; verdict `.value` used consistently.
- **Verify-at-implementation flags:** `agent.model_tier` vs `agent.tier`; `MODEL_TIER_IDS` import path;
  `record_approval_decision` exact signature; the `wrap_tool_call` test-invocation helper location.
