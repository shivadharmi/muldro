# Step 6C — Cross-Path Write Lock + Kill Operator + Fast-Path Guard + Trust Relocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the write path of the first-principles rebuild: fence concurrent external writes with a cross-path Redis lock, replace the monolithic wide-scope Operator agent with per-step capability-scoped execution, fence the latent write-through-fast-path, relocate the user-approval trust increment to fire on *verified* outcome, and absorb the four in-scope 6B carry-forwards — all while keeping the 6B deep-runtime approval gate **dormant** (no live producer until Step 7/10).

**Architecture:** Four semi-independent threads plus four carry-forwards, sequenced so single-owner-per-file holds:
1. **Cross-path write lock** — one shared helper (`src/services/write_lock.py`) keyed `write:{workspace_id}:{capability}`, injected as (a) a new `write_lock` middleware between `trust_gate` and `jarvis_tool_dispatcher` on the deep path, and (b) a wrapper *outside* the idempotency ledger on the autonomous `execute_tool_fn`. Reads never lock.
2. **Kill Operator** — delete the Operator persona; writes execute through the already-gated machinery with a thin neutral **`executor`** identity scoped **per step** (`resolve_for_step(step.capability)`) instead of the union of all writes. Data migration drops the dangling `agents` row.
3. **Fast-path write guard** — no live fast intent writes today (verified); add a fail-closed regression fence so any future mutating fast intent is forced through the gate+lock rather than the ungated inline loop.
4. **Trust relocation** — move the *positive* user-approval increment out of the HTTP click handler into the `CONFIRMED`-gated verification hook (mirrors the auto-exec model); rejection stays at click.
5. **Carry-forwards** — CF-3 (idempotency DB UNIQUE + the write lock as in-flight fence), CF-5 (commit-ordering in `resume_deep_turn`), CF-1 (persist+re-inject ContextPack on resume), CF-2 (read persisted verdict instead of re-assessing on replay), CF-4 (checkpoint reaper).

**Tech Stack:** Python 3.12, async SQLAlchemy (asyncpg), redis.asyncio, LangGraph/Deep Agents (`langchain.agents.middleware.wrap_tool_call`), Alembic, pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio). Run everything from `backend/` via `uv run`.

---

## How to work this plan

- **Infra:** `docker compose up -d postgres redis qdrant` first. All commands from `backend/` via `uv run` (NO pip; `uv sync --all-extras` if deps missing). Full gate: `uv run pytest tests/ --ignore=tests/e2e`. Do **not** edit `backend/` while a `uvicorn --reload` worker runs.
- **Baseline (verified 2026-07-07):** branch `rebuild/first-principles`, HEAD `7621155`, clean tree, single alembic head `c7d3e4f5a6b8`. `uv run pytest tests/ --ignore=tests/e2e` = **3176 passed / 18 skipped**. Every task must keep the suite green (or grow it).
- **Test harness idioms (repo-specific — do NOT use pytest-asyncio):**
  - Async tests are plain `async def test_*`; the repo's `pytest_pyfunc_call` hook runs them under `asyncio.run`.
  - Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`. Use `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`.
  - **Real-DB / real-Redis tests** use the self-contained idiom already in `tests/test_deep_gate_*.py`: a module-level `_db_reachable()` / `_redis_reachable()` probe + `pytest.mark.skipif`, a `NullPool` engine built in-test, and an explicit seed of the User→Workspace FK chain. Copy that scaffold; do NOT invent a `db_session` fixture (there isn't one).
- **Commits:** one conventional-commit per task (`feat:` / `fix:` / `test:` / `refactor:` / `docs:` / `chore:`). **NO `Co-Authored-By` lines.** Do **not** push or merge to `main`.
- **Spike outputs are throwaway:** spike code lives under `backend/spikes/step6c_*/` and is NOT shipped; only its *findings* feed the production tasks. Delete or leave it untracked — never let it gate the suite.
- **Migration discipline:** two migrations in this plan (CF-3 promote-columns; kill-Operator drop-row). Each must be applied+reverted against the live DB by a **single** owner (never two agents racing the DB). Confirm single head after each: `uv run alembic heads` → one `(head)`.

---

## Verified current-state map (file:line — trust this, not the spec)

| Concern | Location | Fact |
|---|---|---|
| Lock primitive | `src/services/locking.py` | `RedisLock` + `distributed_lock(redis, key, ttl=30)` async CM; `SET NX EX`; **unconditional `delete` on release (no fencing token)**; raises `RuntimeError` on contention (no wait). **Zero production callers** (only `tests/test_redis_locking.py`). |
| Deep build seam | `src/orchestrator/agent_invoker.py:158-221` (`_build_deep_agent_for`) | `extra_middleware=(trust_gate, dispatcher)` at `:218`; chain = `capability_scope → trust_gate → dispatcher`. Shared by live (`:262`) + resume (`:414`). |
| Deep dispatcher | `src/deep_runtime/middleware/jarvis_tool_dispatcher.py:66` | `result = await execute_tool(name, args, user_id, workspace_id)` — the deep external write. |
| Deep gate | `src/deep_runtime/middleware/trust_gate.py:206-207` | dormant path: `if not is_gated_source(...): return await handler(request)` → falls through to inner middleware (so a write_lock *inner* of trust_gate fences even dormant/direct writes). |
| Autonomous write | `src/services/dag_runner.py:367-370` (auto-exec) & `:461-464` (approved-resume) | `output = await asyncio.wait_for(self._runner.run_step_action(...), timeout=step_timeout)`. |
| Autonomous exec fn | `src/services/step_runner.py:326-337` | `idem_execute_tool_fn = make_idempotent_execute_tool_fn(self._execute_tool_fn, IdempotencyContext(...))` — the write-lock wrapper layers *outside* this. |
| Operator (hardcoded) | `src/services/step_runner.py:154, 300` | `AGENTS.get("operator")`; `build_operator_tools()` filters by `operator.capability_scope` (`:158`); `run_step_via_agent_loop` uses `operator.prompt` (`:309`), `agent=operator` (`:350`). |
| Operator definition | `src/orchestrator/agents.py:21` (model), `:106-165` (`AGENT_CAPABILITY_SCOPES["operator"]`, widest), `:195` (thinking) | Instantiated generically by `create_sub_agents()` (`:226-240`). |
| Operator prompt | `src/orchestrator/prompts.py:543-565` (`OPERATOR_PROMPT`), `:760` (`AGENT_PROMPTS["operator"]`), `:22` + `:29` (soul-core table row + rule 2) | `:760` is what makes the agent exist. |
| Operator seed | `src/services/agent_registry.py:27, 37, 49-100` | `seed_defaults()` **creates/updates only, never deletes** → dangling row needs a migration. |
| Chat write routing | `src/services/capability_resolver.py:118, 140` | write branch: `return "operator"` at `:140`. |
| Context enrichment set | `src/orchestrator/context_assembler.py:26` | `CONTEXT_ENRICHED_AGENTS` includes `"operator"`. |
| Fast intents | `src/orchestrator/intent_classifier.py:72` (`FAST_INTENTS`, 10), `intent_to_plan()` `:158-230` | **all 10 map to `respond`/`reason`/`perceive`/`knowledge.search` — none write.** `_match_read_capability` no longer exists. |
| Fast-path route decision | `src/orchestrator/chat_processor.py:343-349` (`use_planner`), `:386-388` (`intent_to_plan`), `:440-490` (ungated inline exec) | entire chat path is ungated (never touches GraphExecutor/TrustEngine). |
| User-approval increment (CLICK) | `src/api/routes_approvals.py:184-198` (approve), `:460-471` (reject) | `record_approval_decision(db, ws, capability, risk, decision_type)` fires synchronously at click, before the write runs. |
| Trust increment impl | `src/services/risk_assessor.py:292-316` (`record_approval_decision`) | `approved`→`approved_count++`; `modified`→`modified_count++` + `approved_count++`; `rejected`→`apply_rejection`. |
| Auto-exec increment (MODEL) | `src/services/dag_runner.py:432-441` | `if verdict == VerifyVerdict.CONFIRMED: await self._trust_gate.record_auto_execution_outcome(...)`. |
| Approved-resume verify hook | `src/services/dag_runner.py:499-503` | `risk = await self._trust_gate.assess_step_risk(...)`; `await self._finalize_with_verification(...)` (returns a verdict, currently discarded). |
| Deferred confirm increment | `src/services/scheduler/deferred_verification_tick.py:80-104` | on later CONFIRMED: `transition_step("completed")` + SAVEPOINT `record_approval_decision(..., "approved")`. |
| Approval model | `src/models/approvals.py:10-43` | `artifact_refs` JSONB holds `thread_id`/`tool_call_id`/`agent_name`/`capability`; **no `thread_id`/`tool_call_id` columns, no UNIQUE, no GIN index**; indexes `ix_approvals_user_status`, `ix_approvals_run_status`. Status encodes decision (no `decision` col). |
| TrustState model | `src/models/trust_state.py:11-32` | `approved_count`/`rejected_count`/`modified_count`/`trust_level`/`cooldown_until`; has `uq_trust_state(workspace_id, capability, risk_level)`. |
| CF-3 get-or-create | `src/deep_runtime/middleware/trust_gate.py:130-161` | `select(Approval).where(... artifact_refs.op("@>")({thread_id, tool_call_id}))` then `create_approval` + `commit` — non-atomic, no DB fence. |
| CF-5 resume ordering | `src/orchestrator/agent_invoker.py:394-409` | `approval.status = ...; await db.commit()` at `:400` runs **before** `thread_id`/`agent_name` presence (`:402`) + agent-existence (`:406-408`) checks. |
| CF-1 empty context | `src/orchestrator/agent_invoker.py:413` | resume uses `build_system_prompt(agent, "")` — no `assemble_context` (contrast live `:246-251`). |
| CF-2 redundant assess | `src/deep_runtime/middleware/trust_gate.py:213-246` | gated resume replays body → re-runs `_resolve_capability` + `assess_risk` + `TrustEngine.evaluate` before `interrupt()` returns the verdict. |
| CF-4 checkpoint growth | `src/orchestrator/agent_invoker.py:278-285, 427-434` | `durability="sync"` writes checkpoints every superstep; **no reaper/TTL** for `checkpoints`/`checkpoint_writes`/`checkpoint_blobs`. |

---

## Fork resolutions (locked with the user this session)

- **A — Gate stays DORMANT; 6C is backend-only.** Do **not** land the deferred 6B consumers (`POST /v1/jarvis/chat/resume` HTTP endpoint, typed `approval_needed` CoreEvent, frontend chat-approval UX). There is no live producer of gated (autonomous/headless) provenance on the deep runtime until Step 7/10.
- **B — Both paths, key `write:{workspace_id}:{capability}`.** Deep = new middleware between `trust_gate` and `dispatcher`; autonomous = wrapper outside the idempotency ledger. Different capabilities never block each other; reads never lock. TTL a few× the 60 s tool timeout. Spike resolves fencing-token + wait-vs-fail-fast.
- **C — Full removal + neutral `executor`.** Delete the Operator persona (def/seed/prompt/soul-core), repoint chat routing + the autonomous hardcode to per-step capability-scoped execution via a thin `executor`, add a data migration to drop the `operator` `agents` row, update all tests.
- **D — Absorb all four carry-forwards** (CF-3, CF-5, CF-1, CF-2, CF-4). CF-STEP5 (poisoned-session partial-progress loss) is **excluded** (separate autonomous subsystem, already implemented as Phase-1 CF-2; only a residual).

---

## Task DAG & ownership (single-owner-per-file)

```
Phase 0  Spikes (throwaway)
  0.1 write-lock semantics + middleware-non-spanning spike

Phase 1  Cross-path write lock                     owns: write_lock.py (new), agent_invoker.py, step_runner.py, deep_runtime/middleware/write_lock.py (new)
  1.1 shared helper  →  1.2 deep middleware  →  1.3 autonomous wrapper  →  1.4 cross-path guard (real Redis)

Phase 2  CF-3 idempotency DB fence                 owns: models/approvals.py, migration, trust_gate.py
  2.1 migration (promote cols + partial UNIQUE + backfill)  →  2.2 repoint get-or-create

Phase 3  Kill Operator  (2-stage review)           owns: agents.py, prompts.py, capability_resolver.py, step_runner.py, agent_registry.py, context_assembler.py, migration, tests
  3.1 executor identity  →  3.2 repoint routing  →  3.3 rewrite step_runner  →  3.4 seed+context set  →  3.5 drop-row migration  →  3.6 test retarget  →  3.7 blast-radius review

Phase 4  Fast-path write guard                     owns: chat_processor.py, intent_classifier.py, tests
  4.1 fail-closed fence + regression test

Phase 5  Trust relocation                          owns: routes_approvals.py, dag_runner.py, deferred_verification_tick.py, models/approvals.py (reuse)
  5.1 stop click increment + persist decision_type  →  5.2 CONFIRMED-gated increment (inline + deferred)

Phase 6  Carry-forwards                            owns: agent_invoker.py (reuse), trust_gate.py (reuse), checkpointer reaper
  6.1 CF-5 commit-ordering  →  6.2 CF-1 context re-inject  →  6.3 CF-2 persisted verdict  →  6.4 CF-4 reaper

Phase 7  Holistic opus review + full gate
```

**Sequencing constraint:** `step_runner.py` is touched by 1.3 (lock wrapper) and 3.3 (kill-Operator). `agent_invoker.py` is touched by 1.2, 6.1, 6.2. Same-file tasks run **sequentially** (never two agents on one file). Phases 1→2→3→4→5→6 are ordered; within a phase, steps are ordered.

---

# Phase 0 — Spikes (throwaway; findings feed Phase 1)

### Task 0.1: Prove the write-lock mechanism before building on it

**Why a spike:** The lock's correctness depends on three unproven assumptions. 6B's Task-0 spike *disproved* the plan's core assumption (interrupt detection) — do not skip this.
1. **Middleware non-spanning:** a `write_lock` middleware placed *inner* of `trust_gate` must NOT be entered on the pre-approval pass (the interrupt raises in `trust_gate` before it calls `handler`), so the lock is acquired only around the actual execute on resume — never held across the human wait.
2. **Cross-path mutual exclusion:** a deep-path acquire and an autonomous-path acquire on the same `write:{ws}:{cap}` key genuinely block each other on real Redis; different keys don't.
3. **Release safety:** with the primitive's unconditional `delete`, a TTL-expiry-mid-call lets acquirer B delete acquirer A's lock. Decide: TTL margin alone, or an owner-token compare-and-delete.

**Files:**
- Create: `backend/spikes/step6c_write_lock/probe.py` (throwaway)
- Create: `backend/spikes/step6c_write_lock/FINDINGS.md`

- [ ] **Step 1: Write the probe** — three async experiments against a live Redis (`redis.asyncio.from_url(settings.redis_url)`):

```python
# backend/spikes/step6c_write_lock/probe.py — THROWAWAY. Run manually, do not ship.
import asyncio, uuid
import redis.asyncio as redis_async
from src.config.settings import get_settings

async def exp1_mutual_exclusion(r):
    """Same key blocks; different key does not."""
    key = f"write:ws1:email.send:{uuid.uuid4()}"
    a = await r.set(f"lock:{key}", "A", nx=True, ex=120)
    b = await r.set(f"lock:{key}", "B", nx=True, ex=120)          # must be None (blocked)
    c = await r.set(f"lock:{key}-other", "C", nx=True, ex=120)    # must succeed
    print("exp1", bool(a), b is None, bool(c))                    # expect: True True True
    await r.delete(f"lock:{key}", f"lock:{key}-other")

async def exp2_token_release(r):
    """Owner-token CAS release vs unconditional delete."""
    key = f"lock:test:{uuid.uuid4()}"
    tokenA = uuid.uuid4().hex
    await r.set(key, tokenA, nx=True, ex=1)
    await asyncio.sleep(1.2)                                      # A's lock expires
    tokenB = uuid.uuid4().hex
    await r.set(key, tokenB, nx=True, ex=120)                    # B acquires
    # A's naive release would delete B's lock. CAS release must NOT:
    lua = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
    deleted = await r.eval(lua, 1, key, tokenA)                  # expect 0 (A no longer owns)
    still = await r.get(key)                                     # expect tokenB present
    print("exp2", deleted == 0, still is not None)               # expect: True True
    await r.delete(key)

async def main():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    await exp1_mutual_exclusion(r)
    await exp2_token_release(r)
    await r.aclose()

asyncio.run(main())
```

- [ ] **Step 2: Run it** — `uv run python spikes/step6c_write_lock/probe.py`. Expected: `exp1 True True True`, `exp2 True True`.
- [ ] **Step 3: Prove the middleware-non-spanning assumption *statically*** — read `trust_gate.py:196-268` and confirm by inspection that `interrupt()` (`:255`) is reached *before* any `handler(request)` on the first pass, and that on the dormant path `handler` is called at `:207`. Write the conclusion into `FINDINGS.md`: "a write_lock middleware inner of trust_gate is entered only when trust_gate calls `handler` — i.e. after approval (or immediately on the dormant path); it never wraps the interrupt suspension." (No runtime graph needed — the ordering is a code fact; the spike records the reasoning so the impl task can rely on it.)
- [ ] **Step 4: Write `FINDINGS.md`** with the three decisions the impl tasks consume:
  - TTL = **120 s** (2× the 60 s `asyncio.wait_for` tool timeout in `agent_loop`).
  - Release = **owner-token compare-and-delete** (Lua CAS) — the unconditional delete is unsafe under TTL expiry. Phase 1.1 implements this.
  - Contention = **bounded wait then fail-closed**: poll-acquire up to ~5 s, then return a structured retryable tool error (do NOT silently proceed). Rationale: a live chat write shouldn't hard-fail on a transient same-capability overlap, but must never bypass the lock.
- [ ] **Step 5: No commit of spike code.** Leave `backend/spikes/step6c_write_lock/` untracked (it must not gate the suite). Optionally `git add` only `FINDINGS.md` under the plan if you want it in history — otherwise capture the three decisions in Task 1.1's docstring.

---

# Phase 1 — Cross-path write lock

### Task 1.1: Shared write-lock helper

**Files:**
- Create: `src/services/write_lock.py`
- Test: `tests/test_write_lock.py`

- [ ] **Step 1: Write the failing test** (real Redis; skip if unreachable):

```python
# tests/test_write_lock.py
import asyncio
import uuid
import pytest
import redis.asyncio as redis_async
from src.config.settings import get_settings
from src.services.write_lock import write_lock_key, acquire_write_lock, WRITE_LOCK_TTL_SECONDS


def _redis_reachable() -> bool:
    try:
        import redis
        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


def test_write_lock_key_is_deterministic_and_capability_scoped():
    assert write_lock_key("ws1", "email.send") == "write:ws1:email.send"
    assert write_lock_key("ws1", "email.send") != write_lock_key("ws1", "calendar.create")


async def test_same_key_mutually_excludes_different_key_does_not():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"email.send.{uuid.uuid4().hex}"
    async with acquire_write_lock(r, "ws1", cap):
        # A different capability must acquire immediately.
        async with acquire_write_lock(r, "ws1", f"calendar.{uuid.uuid4().hex}"):
            pass
        # The SAME key must fail fast within the bounded wait.
        with pytest.raises(WriteLockContended):
            async with acquire_write_lock(r, "ws1", cap, wait_timeout=0.5):
                pass
    await r.aclose()


async def test_release_uses_owner_token_and_survives_ttl_expiry_of_a_prior_owner():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"cap.{uuid.uuid4().hex}"
    # Simulate a stale expired lock, then a fresh owner; releasing the FIRST must not delete the SECOND.
    async with acquire_write_lock(r, "ws1", cap):
        current = await r.get(f"lock:{write_lock_key('ws1', cap)}")
        assert current is not None  # a token, not a constant "1"
    assert await r.get(f"lock:{write_lock_key('ws1', cap)}") is None  # released
    await r.aclose()
```

- [ ] **Step 2: Run — expect ImportError** (`write_lock` module missing). `uv run pytest tests/test_write_lock.py -v`.
- [ ] **Step 3: Implement** `src/services/write_lock.py` (owner-token CAS release + bounded wait; decisions from Task 0.1):

```python
"""Cross-path per-(workspace, capability) write lock (Step 6C).

Both the deep-runtime dispatcher and the autonomous DAG path acquire the SAME key so a
chat write and a scheduler write to the same capability in one workspace mutually exclude.
Reads never lock. Keyed on capability (not tool name) per the spec — two tools sharing a
capability serialize; two different capabilities never block each other.

Correctness (proven by spikes/step6c_write_lock): the base primitive's unconditional
release deletes ANOTHER owner's lock after a TTL-expiry-mid-call, so release is an
owner-token compare-and-delete (Lua CAS). Contention is bounded-wait then fail-closed.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

WRITE_LOCK_TTL_SECONDS = 120  # 2x the 60s agent_loop tool timeout — must exceed max call length
_WAIT_TIMEOUT_DEFAULT = 5.0
_POLL_INTERVAL = 0.05

# Compare-and-delete: only the owner (matching token) may release.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class WriteLockContended(RuntimeError):
    """Raised when the write lock could not be acquired within ``wait_timeout``."""


def write_lock_key(workspace_id: str, capability: str) -> str:
    """Deterministic key shared by BOTH execution paths — do not change independently."""
    return f"write:{workspace_id}:{capability}"


@asynccontextmanager
async def acquire_write_lock(
    redis,
    workspace_id: str,
    capability: str,
    *,
    ttl: int = WRITE_LOCK_TTL_SECONDS,
    wait_timeout: float = _WAIT_TIMEOUT_DEFAULT,
):
    """Acquire the per-(workspace, capability) write lock, bounded-wait then fail-closed.

    Raises ``WriteLockContended`` if not acquired within ``wait_timeout``. Releases via an
    owner-token CAS so a lock that expired and was re-acquired by another owner is never
    deleted out from under them.
    """
    redis_key = f"lock:{write_lock_key(workspace_id, capability)}"
    token = uuid.uuid4().hex
    deadline = asyncio.get_event_loop().time() + wait_timeout
    acquired = False
    while True:
        acquired = bool(await redis.set(redis_key, token, nx=True, ex=ttl))
        if acquired:
            break
        if asyncio.get_event_loop().time() >= deadline:
            raise WriteLockContended(
                f"write lock contended: {write_lock_key(workspace_id, capability)}"
            )
        await asyncio.sleep(_POLL_INTERVAL)
    try:
        yield
    finally:
        try:
            await redis.eval(_RELEASE_LUA, 1, redis_key, token)
        except Exception:
            # Best-effort release; TTL guarantees eventual expiry.
            pass
```

- [ ] **Step 4: Run — expect PASS** (also add `from src.services.write_lock import WriteLockContended` to the test imports).
- [ ] **Step 5: Commit** — `git add src/services/write_lock.py tests/test_write_lock.py && git commit -m "feat(rebuild): cross-path write-lock helper — capability-keyed, owner-token CAS release (Step 6C Task 1.1)"`.

---

### Task 1.2: Deep-path `write_lock` middleware

**Files:**
- Create: `src/deep_runtime/middleware/write_lock.py`
- Modify: `src/orchestrator/agent_invoker.py:205-221` (wire the middleware into `_build_deep_agent_for`)
- Test: `tests/deep_runtime/test_write_lock_middleware.py`

- [ ] **Step 1: Write the failing test** — a write capability acquires the lock around dispatch; a read passes through untouched:

```python
# tests/deep_runtime/test_write_lock_middleware.py
import json
import pytest
from langchain_core.messages import ToolMessage
from src.deep_runtime.middleware.write_lock import make_write_lock_middleware


class _FakeRedis:
    def __init__(self): self.calls = []
    async def set(self, k, v, nx=None, ex=None): self.calls.append(("set", k)); return True
    async def eval(self, *a): self.calls.append(("eval",)); return 1


class _Req:
    def __init__(self, name): self.tool_call = {"name": name, "id": "tc1", "args": {}}


async def test_write_capability_acquires_lock_around_handler():
    redis = _FakeRedis()
    async def resolve_capability(name): return "email.send"       # a write capability
    async def handler(req): return ToolMessage(content="ok", tool_call_id="tc1", name="x")
    mw = make_write_lock_middleware(workspace_id="ws1", redis=redis,
                                    resolve_capability=resolve_capability)
    result = await mw.wrap_tool_call(_Req("send_email"), handler)  # invoke the hook
    assert result.content == "ok"
    assert ("set", "lock:write:ws1:email.send") in redis.calls     # lock acquired
    assert ("eval",) in redis.calls                                # lock released


async def test_read_capability_bypasses_lock():
    redis = _FakeRedis()
    async def resolve_capability(name): return "email.read"        # read-only
    async def handler(req): return ToolMessage(content="ok", tool_call_id="tc1", name="x")
    mw = make_write_lock_middleware(workspace_id="ws1", redis=redis,
                                    resolve_capability=resolve_capability)
    await mw.wrap_tool_call(_Req("list_email"), handler)
    assert redis.calls == []                                       # NEVER locked a read
```

- [ ] **Step 2: Run — expect ImportError.** `uv run pytest tests/deep_runtime/test_write_lock_middleware.py -v`.
- [ ] **Step 3: Implement** `src/deep_runtime/middleware/write_lock.py`:

```python
"""Deep-runtime write-lock middleware (Step 6C).

Placed BETWEEN trust_gate (OUTER) and jarvis_tool_dispatcher (INNER):
    capability_scope → trust_gate → write_lock → dispatcher
So it runs AFTER approval (trust_gate calls handler only post-approve, or immediately on the
dormant direct path) and IMMEDIATELY BEFORE execute_tool — never across the interrupt wait.
Reads and built-ins pass straight through. The lock key is shared with the autonomous path
(src.services.write_lock) so a chat write and a scheduler write to the same capability
mutually exclude.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.capabilities import is_read_only_capability
from src.services.write_lock import WriteLockContended, acquire_write_lock

logger = logging.getLogger(__name__)

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]


def make_write_lock_middleware(
    *,
    workspace_id: str,
    redis,
    resolve_capability: ResolveCapabilityFn,
) -> AgentMiddleware:
    """Build the per-turn write-lock middleware. ``workspace_id`` is closure-captured
    (never LLM-supplied). ``resolve_capability(name) -> capability|None`` maps a tool name
    to its capability via the registry (same resolution the autonomous path uses)."""

    @wrap_tool_call
    async def write_lock(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if redis is None:
            # No Redis wired — fall through (the lock is a safety fence, not a hard dep on
            # the dormant/legacy path). Logged once so degradation is visible.
            return await handler(request)

        capability = await resolve_capability(name)
        if not capability or is_read_only_capability(capability):
            return await handler(request)  # reads never lock

        try:
            async with acquire_write_lock(redis, workspace_id, capability):
                return await handler(request)
        except WriteLockContended:
            logger.warning("[deep_runtime] write lock contended for %s (%s)", name, capability)
            return ToolMessage(
                content=json.dumps(
                    {"error": "resource busy — another write is in progress, retry", "blocked": True}
                ),
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )

    return write_lock
```

- [ ] **Step 4: Wire it into `_build_deep_agent_for`** (`agent_invoker.py`). After the `dispatcher = make_jarvis_tool_dispatcher(...)` block (`:205-209`), add the middleware and change the tuple at `:218`:

```python
        # Step 6C: cross-path write lock, placed INNER of trust_gate, OUTER of dispatcher.
        # Resolve capability with the SAME registry lookup trust_gate uses, so the lock key
        # matches the autonomous path exactly.
        from src.deep_runtime.middleware.trust_gate import _resolve_capability
        from src.deep_runtime.middleware.write_lock import make_write_lock_middleware

        async def _resolve_cap(name: str):
            _ok, cap = await _resolve_capability(name, workspace_id, self._db_factory)
            return cap

        write_lock = make_write_lock_middleware(
            workspace_id=workspace_id,
            redis=getattr(self._services, "redis", None),
            resolve_capability=_resolve_cap,
        )
        return await build_deep_agent(
            agent,
            shells,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            extra_middleware=(trust_gate, write_lock, dispatcher),  # was (trust_gate, dispatcher)
            system_prompt=system_prompt,
            checkpointer=self._checkpointer_provider() or MemorySaver(),
        )
```

- [ ] **Step 5: Run** the new test + the deep-runtime suite: `uv run pytest tests/deep_runtime/ tests/test_deep_gate_end_to_end.py -v`. Expected: PASS (the frozen SSE shapes are unchanged; a dormant direct read still never locks, a dormant direct write now acquires+releases — verify no SSE frame shape changed).
- [ ] **Step 6: Commit** — `git commit -m "feat(rebuild): deep-path write-lock middleware between trust_gate and dispatcher (Step 6C Task 1.2)"`.

---

### Task 1.3: Autonomous-path lock wrapper

**Files:**
- Modify: `src/services/step_runner.py:316-337` (wrap `execute_tool_fn` outside the idempotency ledger)
- Modify: `src/services/step_runner.py` constructor (accept a `redis` client)
- Modify: `src/services/graph_executor.py` (pass `redis` into `StepRunner`)
- Test: `tests/test_step_runner_write_lock.py`

- [ ] **Step 1: Write the failing test** — an autonomous write goes through the lock wrapper; the lock key matches the deep path's:

```python
# tests/test_step_runner_write_lock.py
import pytest
from src.services.write_lock import write_lock_key
from src.services.step_runner import make_lock_wrapped_execute_tool_fn


class _FakeRedis:
    def __init__(self): self.keys = []
    async def set(self, k, v, nx=None, ex=None): self.keys.append(k); return True
    async def eval(self, *a): return 1


async def test_autonomous_write_acquires_the_same_key_as_deep_path():
    redis = _FakeRedis()
    async def inner(name, args, user_id, ws): return {"ok": True}
    async def resolve_capability(name): return "email.send"
    fn = make_lock_wrapped_execute_tool_fn(inner, redis=redis, workspace_id="ws1",
                                           resolve_capability=resolve_capability)
    await fn("send_email", {}, "u1", "ws1")
    assert f"lock:{write_lock_key('ws1', 'email.send')}" in redis.keys


async def test_autonomous_read_does_not_lock():
    redis = _FakeRedis()
    async def inner(name, args, user_id, ws): return {"ok": True}
    async def resolve_capability(name): return "email.read"
    fn = make_lock_wrapped_execute_tool_fn(inner, redis=redis, workspace_id="ws1",
                                           resolve_capability=resolve_capability)
    await fn("list_email", {}, "u1", "ws1")
    assert redis.keys == []
```

- [ ] **Step 2: Run — expect ImportError.**
- [ ] **Step 3: Implement `make_lock_wrapped_execute_tool_fn`** in `step_runner.py` (module-level helper, near `make_idempotent_execute_tool_fn` import site):

```python
def make_lock_wrapped_execute_tool_fn(inner_fn, *, redis, workspace_id, resolve_capability):
    """Wrap an execute_tool_fn so external WRITES acquire the cross-path write lock
    (src.services.write_lock) — same key as the deep-runtime middleware. Reads pass through.
    Layered OUTSIDE the idempotency ledger so the lock serializes the whole write attempt
    (idempotency check + execute)."""
    from src.integrations.capabilities import is_read_only_capability
    from src.services.write_lock import WriteLockContended, acquire_write_lock

    async def _wrapped(name, args, user_id, ws):
        if redis is None:
            return await inner_fn(name, args, user_id, ws)
        capability = await resolve_capability(name)
        if not capability or is_read_only_capability(capability):
            return await inner_fn(name, args, user_id, ws)
        try:
            async with acquire_write_lock(redis, workspace_id, capability):
                return await inner_fn(name, args, user_id, ws)
        except WriteLockContended:
            return {"error": "resource busy — another write is in progress, retry", "blocked": True}

    return _wrapped
```

- [ ] **Step 4: Layer it in `run_step_via_agent_loop`** — after the `idem_execute_tool_fn = make_idempotent_execute_tool_fn(...)` block (`:326-337`), wrap once more:

```python
        # Step 6C: fence writes with the cross-path lock, OUTSIDE the idempotency ledger.
        if idem_execute_tool_fn is not None and self._redis is not None:
            async def _resolve_cap(tool_name: str):
                td = await self._tool_registry.get_tool(tool_name) if self._tool_registry else None
                return getattr(td, "capability", None) if td else None
            idem_execute_tool_fn = make_lock_wrapped_execute_tool_fn(
                idem_execute_tool_fn, redis=self._redis,
                workspace_id=run.workspace_id or "", resolve_capability=_resolve_cap,
            )
```

- [ ] **Step 5: Thread `redis` into `StepRunner`** — add `redis=None` to `StepRunner.__init__` (store `self._redis`), and pass it from `graph_executor.py` where `StepRunner(...)` is built (`:127-139`). Use the same `services.redis` the rest of the stack uses.
- [ ] **Step 6: Run** — `uv run pytest tests/test_step_runner_write_lock.py tests/test_step_runner.py tests/test_graph_executor.py -v`. Expected: PASS (existing StepRunner tests unaffected — `redis=None` default keeps them lock-free).
- [ ] **Step 7: Commit** — `git commit -m "feat(rebuild): autonomous-path write-lock wrapper on execute_tool_fn, shared key with deep path (Step 6C Task 1.3)"`.

---

### Task 1.4: Cross-path mutual-exclusion guard (load-bearing, real Redis)

**Files:**
- Test: `tests/test_write_lock_cross_path.py`

- [ ] **Step 1: Write the guard** — prove a deep-path key and an autonomous-path key for the **same** `(ws, capability)` collide on real Redis, and that a NEGATIVE control (different capability) does not:

```python
# tests/test_write_lock_cross_path.py  — LOAD-BEARING guard for Fork B.
import uuid
import pytest
import redis.asyncio as redis_async
from src.config.settings import get_settings
from src.services.write_lock import write_lock_key, acquire_write_lock, WriteLockContended


def _redis_reachable() -> bool:
    try:
        import redis
        redis.from_url(get_settings().redis_url).ping(); return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


async def test_deep_and_autonomous_paths_mutually_exclude_same_capability():
    """The deep middleware and the autonomous wrapper BOTH call acquire_write_lock with the
    same write_lock_key — so holding one blocks the other. This is the whole point of Fork B."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"email.send.{uuid.uuid4().hex}"
    async with acquire_write_lock(r, "wsX", cap):                 # "deep path" holds it
        with pytest.raises(WriteLockContended):                   # "autonomous path" blocked
            async with acquire_write_lock(r, "wsX", cap, wait_timeout=0.3):
                pass
    # NEGATIVE control: after release, the same key acquires cleanly.
    async with acquire_write_lock(r, "wsX", cap, wait_timeout=0.3):
        pass
    await r.aclose()
```

- [ ] **Step 2: Run — expect PASS** (with Redis up). `uv run pytest tests/test_write_lock_cross_path.py -v`.
- [ ] **Step 3: Commit** — `git commit -m "test(rebuild): cross-path write-lock mutual-exclusion guard with negative control (Step 6C Task 1.4)"`.

---

# Phase 2 — CF-3: idempotency DB fence

### Task 2.1: Migration — promote `thread_id`/`tool_call_id` to columns + partial UNIQUE + backfill

**Why:** The get-or-create idempotency key lives in `artifact_refs` JSONB with no DB fence — two concurrent replays can both create a pending Approval. The write lock is only a partial in-flight fence (and its key is capability, not the idempotency tuple). The true fix is promoted columns + a partial UNIQUE index.

**Files:**
- Modify: `src/models/approvals.py:10-43` (add nullable `thread_id`, `tool_call_id` columns + a partial unique index)
- Create: `backend/alembic/versions/<rev>_approval_idempotency_columns.py`
- Test: `tests/test_approval_idempotency_constraint.py`

- [ ] **Step 1: Write the failing real-DB test** (self-contained NullPool + FK seed idiom):

```python
# tests/test_approval_idempotency_constraint.py — real DB.
import uuid
import pytest
from sqlalchemy.exc import IntegrityError
# ... _db_reachable() probe + NullPool engine + seed_user_workspace() as in tests/test_deep_gate_*.py

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="requires live Postgres")


async def test_duplicate_thread_tool_call_is_rejected_by_unique_index(db, ws_id, user_id):
    from src.services.approval_service import create_approval
    refs = {"thread_id": "chat_abc", "tool_call_id": "tc_1", "capability": "email.send"}
    await create_approval(db, user_id=user_id, workspace_id=ws_id, approval_type="tool:x",
                          title="t", summary="s", risk_level="high", requested_by=user_id,
                          run_id=None, step_id=None, artifact_refs=refs)
    await db.commit()
    with pytest.raises(IntegrityError):
        await create_approval(db, user_id=user_id, workspace_id=ws_id, approval_type="tool:x",
                              title="t", summary="s", risk_level="high", requested_by=user_id,
                              run_id=None, step_id=None, artifact_refs=refs)
        await db.commit()
```

- [ ] **Step 2: Run — expect the test to FAIL** (no constraint yet → both inserts succeed, no `IntegrityError`).
- [ ] **Step 3: Add columns + index to the model** (`src/models/approvals.py`):

```python
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_approvals_user_status", "user_id", "status", "created_at"),
        Index("ix_approvals_run_status", "run_id", "status"),
        # Partial UNIQUE: only rows that carry the deep-gate idempotency tuple are fenced;
        # legacy/autonomous approvals (NULL thread_id/tool_call_id) are unaffected.
        Index(
            "uq_approvals_thread_tool_call",
            "workspace_id", "thread_id", "tool_call_id",
            unique=True,
            postgresql_where=text("thread_id IS NOT NULL AND tool_call_id IS NOT NULL"),
        ),
    )
```

(Add `from sqlalchemy import text` if not present.)

- [ ] **Step 4: Generate + hand-verify the migration** — `uv run alembic revision --autogenerate -m "approval idempotency columns"`. Then EDIT it to: (a) `add_column` `thread_id`/`tool_call_id`; (b) **backfill** from JSONB — `UPDATE approvals SET thread_id = artifact_refs->>'thread_id', tool_call_id = artifact_refs->>'tool_call_id' WHERE artifact_refs ? 'thread_id'`; (c) create the partial unique index with the `postgresql_where`. Downgrade drops index + columns. Confirm `down_revision = "c7d3e4f5a6b8"`.
- [ ] **Step 5: Apply + round-trip against the live DB (single owner — no concurrent DB access):**

```bash
uv run alembic upgrade head
uv run alembic heads          # expect ONE (head), = the new rev
uv run alembic downgrade -1
uv run alembic upgrade head
```

- [ ] **Step 6: Run the test — expect PASS** (`IntegrityError` on the duplicate).
- [ ] **Step 7: Commit** — `git commit -m "feat(rebuild): promote deep-gate idempotency tuple to Approval columns + partial UNIQUE index (Step 6C Task 2.1, CF-3)"`.

---

### Task 2.2: Repoint the get-or-create to write the columns + tolerate the UNIQUE

**Files:**
- Modify: `src/services/approval_service.py` (`create_approval` writes `thread_id`/`tool_call_id` columns from `artifact_refs`)
- Modify: `src/deep_runtime/middleware/trust_gate.py:130-161` (query by columns; catch `IntegrityError` → re-select)
- Test: `tests/deep_runtime/test_trust_gate_idempotency.py` (extend existing)

- [ ] **Step 1: Write the failing test** — a concurrent get-or-create yields exactly one Approval even when both miss the initial SELECT:

```python
async def test_get_or_create_is_atomic_under_unique_constraint(...):
    # Two _decide_and_maybe_persist calls with the same (ws, thread_id, tool_call_id):
    # first creates; second hits IntegrityError, re-selects, returns the SAME approval_id.
    a1 = await _decide_and_maybe_persist(name="send_email", capability="email.send", risk=high_risk,
                                         workspace_id=ws, user_id=u, thread_id="t1",
                                         tool_call_id="tc1", agent_name="executor", db_factory=dbf)
    a2 = await _decide_and_maybe_persist(name="send_email", capability="email.send", risk=high_risk,
                                         workspace_id=ws, user_id=u, thread_id="t1",
                                         tool_call_id="tc1", agent_name="executor", db_factory=dbf)
    assert a1[1] == a2[1]  # same approval_id, one row
```

- [ ] **Step 2: Run — expect FAIL** (second insert raises `IntegrityError` uncaught).
- [ ] **Step 3: Implement** — `create_approval` sets `thread_id=artifact_refs.get("thread_id")`, `tool_call_id=artifact_refs.get("tool_call_id")` on the row. In `_decide_and_maybe_persist`, change the SELECT to query the columns (`Approval.thread_id == thread_id, Approval.tool_call_id == tool_call_id`) and wrap the `create_approval`+`commit` in `try/except IntegrityError: await db.rollback(); <re-select and return existing>`.
- [ ] **Step 4: Run — expect PASS.** Also run `tests/deep_runtime/ tests/test_deep_gate_*` to confirm no regression.
- [ ] **Step 5: Commit** — `git commit -m "fix(rebuild): atomic get-or-create Approval via promoted columns + IntegrityError re-select (Step 6C Task 2.2, CF-3)"`.

---

# Phase 3 — Kill Operator (2-stage review)

> **Blast radius:** ~17–22 source sites across ~10 files + 26 test files + a data migration. Operator is the sole write agent with the widest scope and **no sibling**. Replacement = a thin neutral **`executor`** identity that carries the write capabilities as a capability-scope *backstop* but is OFFERED only the current step's tools (`resolve_for_step(step.capability)`). The concrete security win: today `build_operator_tools()` offers **every** write tool to **every** autonomous step; after this, a step is offered only its own capability's tools.

### Task 3.1: Introduce the neutral `executor` identity

**Files:**
- Modify: `src/orchestrator/agents.py:21` (model tier), `:106-165` (rename scope key `operator`→`executor`), `:195` (thinking)
- Modify: `src/orchestrator/prompts.py:543-565` (`OPERATOR_PROMPT`→`EXECUTOR_PROMPT`), `:760` (`AGENT_PROMPTS` key), `:22` + `:29` (soul-core table row + rule 2)
- Test: `tests/test_executor_agent.py` (new), update `tests/test_agent_registry.py`

- [ ] **Step 1: Write the failing test** — the roster has `executor`, not `operator`:

```python
# tests/test_executor_agent.py
from src.orchestrator.agents import AGENTS, AGENT_CAPABILITY_SCOPES, AGENT_MODEL_TIERS


def test_executor_replaces_operator_in_roster():
    assert AGENTS.get("operator") is None
    ex = AGENTS.get("executor")
    assert ex is not None
    assert AGENT_MODEL_TIERS["executor"] == "sonnet"
    assert "operator" not in AGENT_CAPABILITY_SCOPES
    # executor still carries the write capabilities as a scope backstop
    assert "email.send" in AGENT_CAPABILITY_SCOPES["executor"]


def test_soul_core_no_longer_names_operator():
    from src.orchestrator.prompts import JARVIS_SOUL_CORE
    assert "Operator" not in JARVIS_SOUL_CORE
    assert "Executor" in JARVIS_SOUL_CORE
```

- [ ] **Step 2: Run — expect FAIL** (`operator` still present).
- [ ] **Step 3: Implement** — rename every `operator` key to `executor` in `agents.py` (model tier, `AGENT_CAPABILITY_SCOPES`, thinking) and `prompts.py`. `EXECUTOR_PROMPT` keeps Operator's "execute approved plans via tools" role text but reworded neutrally (no "Operator" noun). In `JARVIS_SOUL_CORE`: change the agent-table row `| Operator | Execute approved plans via tools |` → `| Executor | Execute approved plans via tools, scoped per step |`, and rule 2 `Only Operator touches external write tools` → `Only the Executor touches external write tools (scoped to the step's capability)`.
- [ ] **Step 4: Run — expect PASS** (`tests/test_executor_agent.py`, `tests/test_agent_registry.py`).
- [ ] **Step 5: Commit** — `git commit -m "refactor(rebuild): replace Operator persona with neutral executor identity (Step 6C Task 3.1)"`.

---

### Task 3.2: Repoint chat write-routing to `executor`

**Files:**
- Modify: `src/services/capability_resolver.py:118, 140` (`return "operator"` → `return "executor"`; update the doc comment)
- Modify: `src/services/runtime_projection.py:206` (cosmetic — reflects whatever the classifier returns; no logic change, just confirm the active-agents display reads `executor`)
- Test: update `tests/test_capability_resolver.py:245-251`

- [ ] **Step 1: Update the failing test** — `route_step("email.send")` now resolves to `"executor"`:

```python
async def test_write_capability_routes_to_executor():
    resolver = CapabilityResolver(...)
    assert await route_step("email.send", resolver) == "executor"
    assert await route_step("calendar.create", resolver) == "executor"
```

- [ ] **Step 2: Run — expect FAIL** (`"operator"` returned).
- [ ] **Step 3: Implement** — `capability_resolver.py:140` `return "executor"`; update the `# 4. Known write capability ... -> "operator"` comment at `:118`.
- [ ] **Step 4: Run — expect PASS** (`tests/test_capability_resolver.py`, `tests/test_runtime_projection.py`).
- [ ] **Step 5: Commit** — `git commit -m "refactor(rebuild): route write capabilities to executor (Step 6C Task 3.2)"`.

---

### Task 3.3: Rewrite `step_runner` — per-step capability scope, no hardcoded Operator

**Files:**
- Modify: `src/services/step_runner.py:146-198` (`build_operator_tools()` → `build_executor_tools(step_capability)`), `:300-314` (`AGENTS.get("operator")` → `AGENTS.get("executor")`; per-step tools), `run_readback` docstring at `:206-208`
- Test: `tests/test_step_runner.py` (extend) + `tests/test_step_runner_scope.py` (new, load-bearing)

- [ ] **Step 1: Write the failing load-bearing test** — a step is offered ONLY its capability's tools, not the union:

```python
# tests/test_step_runner_scope.py — LOAD-BEARING security guard.
async def test_step_offered_only_its_capability_tools_not_the_union(monkeypatch):
    # A step with capability email.send must NOT be offered calendar.create's tool.
    runner = StepRunner(..., tool_registry=fake_registry_with(["email.send", "calendar.create"]))
    tools = await runner.build_executor_tools("email.send")
    names = {t["name"] for t in tools}
    assert any("email" in n for n in names)
    assert not any("calendar" in n for n in names)   # NEGATIVE control: no cross-capability leak
```

- [ ] **Step 2: Run — expect FAIL** (no `build_executor_tools` yet).
- [ ] **Step 3: Implement** — replace `build_operator_tools(self)` with `build_executor_tools(self, step_capability: str)`. Instead of filtering by `operator.capability_scope`, resolve the step's tool set via `CapabilityResolver.resolve_for_step(step_capability)` (primary tool + related reads) and build API tool defs for only those. In `run_step_via_agent_loop`: `executor = AGENTS.get("executor")` (`:300`), `system_blocks = [{"type":"text","text": executor.prompt}]` (`:309`), `tools = await self.build_executor_tools(step_capability)` where `step_capability = (step.input_data or {}).get("capability", ...)` (same derivation used at `dag_runner.py:499-501`), `agent=executor` (`:350`). Keep the idempotency ledger + Task 1.3 lock wrapper + `auth_required` handling unchanged.
- [ ] **Step 4: Update `run_readback`** — its docstring references `build_operator_tools`; it resolves reads via the registry directly, so only the docstring changes.
- [ ] **Step 5: Run — expect PASS** (`tests/test_step_runner_scope.py`, `tests/test_step_runner.py`, `tests/test_graph_executor.py`).
- [ ] **Step 6: Commit** — `git commit -m "refactor(rebuild): step_runner executes per-step capability-scoped, no hardcoded Operator (Step 6C Task 3.3)"`.

---

### Task 3.4: Seed names + context-enrichment set

**Files:**
- Modify: `src/services/agent_registry.py:27, 37` (`_DEFAULT_DISPLAY_NAMES`/`_DEFAULT_DESCRIPTIONS`: `operator`→`executor`), `:21/:50` stale "8 agents" comment → "7"
- Modify: `src/orchestrator/context_assembler.py:26` (`CONTEXT_ENRICHED_AGENTS`: `"operator"`→`"executor"`)
- Test: update `tests/test_agent_registry.py`

- [ ] **Step 1: Update the failing test** — seed produces `executor` display name/description; no `operator`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** — rename the dict keys; swap `"operator"`→`"executor"` in `CONTEXT_ENRICHED_AGENTS` (else executor steps lose context enrichment); fix the "8 agents" comment.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -m "refactor(rebuild): seed + context-enrichment set reference executor (Step 6C Task 3.4)"`.

---

### Task 3.5: Data migration — drop the dangling `operator` `agents` row

**Why:** `AgentRegistry.seed_defaults()` creates/updates only — it never deletes. On restart it will create `executor` (now in `AGENT_PROMPTS`) but leave the old `operator` row unrouted. A data migration removes it.

**Files:**
- Create: `backend/alembic/versions/<rev>_drop_operator_agent.py`
- Test: `tests/test_operator_removed_migration.py` (real DB, single owner)

- [ ] **Step 1: FK safety check** — grep for any FK to `agents.agent_id` referencing operator (`grep -rn "ForeignKey(\"agents" src/models/`). The extraction found agents referenced by **name string**, not FK, in task_runs/steps — confirm no `ON DELETE` cascade surprises. Record the finding in the migration docstring.
- [ ] **Step 2: Write the migration** — `op.execute("DELETE FROM agents WHERE agent_id = 'operator' OR name = 'operator'")`. Downgrade re-inserts a minimal operator row (or is a no-op documented as irreversible-by-design — prefer a best-effort re-insert so downgrade doesn't strand). `down_revision` = Task 2.1's rev.
- [ ] **Step 3: Write the real-DB test** — after `upgrade`, `SELECT ... WHERE name='operator'` returns zero rows; `executor` present after a seed run.
- [ ] **Step 4: Apply + round-trip (single owner):** `uv run alembic upgrade head && uv run alembic heads && uv run alembic downgrade -1 && uv run alembic upgrade head`. Confirm ONE head.
- [ ] **Step 5: Run the test — expect PASS.**
- [ ] **Step 6: Commit** — `git commit -m "feat(rebuild): drop dangling operator agents row (seed never deletes) (Step 6C Task 3.5)"`.

---

### Task 3.6: Test-suite retarget (26 files)

**Files (the breaking assertions the extraction pinned — update each):**
- `tests/test_tool_normalization.py:7-18` (operator scope shape → executor)
- `tests/test_phase5_capabilities.py:124-130` (`calendar.delete`/`workflow.delete` in scope → executor scope)
- `tests/test_capability_resolver.py:245-251` (already done in 3.2 — verify)
- `tests/test_perceiver_agent.py:72-79`, `tests/test_agent_registry.py:68` (7-agent roster names)
- `tests/test_runtime_projection.py:114-187` (`"operator"` in active agents → `"executor"`)
- `tests/test_chat_pipeline*.py` (routing to operator → executor)
- `tests/test_graph_executor.py:298-322` (`captured_kwargs["agent"].name == "operator"` → `"executor"`)
- `tests/test_orchestrator.py:256-258`
- `tests/deep_runtime/*` + `tests/test_deep_gate_*` (fixtures using `agent_name="operator"` / a mock operator SubAgent as the canonical write agent → `executor`)

- [ ] **Step 1: Enumerate** — `grep -rln '"operator"\|operator\.\|Operator' tests/` → the authoritative list (the extraction estimated ~26 files / ~65 refs; trust the grep, not the estimate).
- [ ] **Step 2: Retarget** — for EACH file, replace the operator identity with `executor` in assertions/fixtures. Do NOT change test intent — a routing test still asserts writes route to the write agent, now named `executor`. Watch for false positives (human-ops "operator" in `runtime_preflight.py`, `app.py`, `routes_health.py`, `background_tasks_tick.py` — those are NOT tests and NOT the agent).
- [ ] **Step 3: Run the FULL gate** — `uv run pytest tests/ --ignore=tests/e2e`. Expected: green, count ≥ 3176 + new tests. Fix any straggler.
- [ ] **Step 4: Commit** — `git commit -m "test(rebuild): retarget operator→executor across the suite (Step 6C Task 3.6)"`.

---

### Task 3.7: Kill-Operator blast-radius review (2-stage parallel + gate)

- [ ] **Step 1: Two-stage parallel review** — dispatch two independent review subagents (verify-don't-trust; reviewers get verified too):
  - **Reviewer A (routing/execution):** confirm every write path (chat `capability_resolver`→`chat_pipeline`→`agent_invoker`; autonomous `dag_runner`→`step_runner`) reaches `executor` and that a step is offered only its capability's tools. Grep for any surviving `AGENTS.get("operator")` / `"operator"` routing in `src/`.
  - **Reviewer B (definition/seed/migration):** confirm no `operator` in `agents.py`/`prompts.py`/`agent_registry.py`/`context_assembler.py`; the migration removes the row; the live-DB round-trip is clean (assign the DB round-trip to Reviewer B ALONE to avoid a DB race).
- [ ] **Step 2: Address findings**, re-run the full gate, commit fixes if any.

---

# Phase 4 — Fast-path write guard

### Task 4.1: Fail-closed fence for a future mutating fast intent

**Why:** No fast intent writes today (all 10 → `respond`/`reason`/`perceive`/`knowledge.search`), but the fast path executes inline **ungated** (`chat_processor.py:440-490`) and skips the Planner. If someone later adds a fast intent whose `intent_to_plan` branch emits a write capability, it would execute with no gate and no lock. Fence it now.

**Files:**
- Modify: `src/orchestrator/chat_processor.py:386-388` (after `intent_to_plan`, assert no synthesized step is a write; if it is, fall back to the Planner path)
- Test: `tests/test_fast_path_no_write.py` (new, regression fence)

- [ ] **Step 1: Write the failing/guard test** — (a) every real fast intent yields a non-write plan; (b) a hypothetical write-emitting fast intent is diverted off the ungated inline path:

```python
# tests/test_fast_path_no_write.py — regression fence for the latent write-through-fast-path.
import pytest
from src.orchestrator.intent_classifier import FAST_INTENTS, intent_to_plan
from src.services.capability_resolver import is_write_capability


@pytest.mark.parametrize("intent", sorted(FAST_INTENTS))
def test_no_fast_intent_emits_a_write_capability(intent):
    plan = intent_to_plan(intent, "do the thing", capabilities=[])
    for step in plan.steps:
        assert not is_write_capability(step.capability), (
            f"fast intent {intent} emitted write capability {step.capability} — "
            "it would execute UNGATED on the inline fast path; route through Planner+gate instead"
        )


def test_fast_path_diverts_a_write_plan_to_the_planner(monkeypatch):
    # If intent_to_plan ever returns a write step, chat_processor must set use_planner=True.
    ...  # patch intent_to_plan to emit a write step; assert the processor takes the gated path
```

- [ ] **Step 2: Run — expect the parametrized test to PASS today** (proves the invariant holds now) and the divert test to FAIL (no guard yet).
- [ ] **Step 3: Implement the divert** in `chat_processor.py` after `plan = intent_to_plan(...)` (`:388`):

```python
            # Fail-closed fence (Step 6C): the fast path is ungated + skips the Planner. If a
            # fast intent ever synthesizes a WRITE capability, do NOT execute it inline — fall
            # back to the Planner path so it goes through GraphExecutor's gate + the write lock.
            if any(is_write_capability(s.capability) for s in plan.steps):
                logger.warning("fast intent %s emitted a write capability — diverting to Planner", intent)
                use_planner = True
                plan = None  # re-planned below on the use_planner branch
```

(Wire this so the subsequent `if use_planner:` branch re-plans; confirm the control flow at `:343-388` supports the late flip.)

- [ ] **Step 4: Run — expect both PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(rebuild): fail-closed fence diverts any mutating fast intent off the ungated inline path (Step 6C Task 4.1)"`.

---

# Phase 5 — Trust-increment relocation

### Task 5.1: Stop the click-time positive increment; persist `decision_type`

**Files:**
- Modify: `src/api/routes_approvals.py:184-198` (approve handler — persist `decision_type`, drop the counter call; keep the status flip + commit)
- Modify: `src/api/routes_approvals.py:460-471` (reject handler — **unchanged**; rejection stays at click)
- Test: `tests/test_trust_increment_relocation.py` (real DB)

- [ ] **Step 1: Write the failing real-DB test** — after approve-click, `TrustState.approved_count` is UNCHANGED (the increment moved); the approval carries `decision_type`; reject still increments at click:

```python
# tests/test_trust_increment_relocation.py — real DB.
async def test_approve_click_does_not_increment_trust_but_records_decision_type(db, ...):
    before = await get_trust_state(db, ws, "email.send", "high")
    await approve_action(approval_id, req=None, db=db, ...)   # user clicks approve
    after = await get_trust_state(db, ws, "email.send", "high")
    assert after.approved_count == before.approved_count      # NOT incremented at click
    appr = await db.get(Approval, approval_id)
    assert (appr.artifact_refs or {}).get("decision_type") == "approved"


async def test_reject_click_still_increments_at_click(db, ...):
    before = await get_trust_state(db, ws, "email.send", "high")
    await reject_action(approval_id, ...)
    after = await get_trust_state(db, ws, "email.send", "high")
    assert after.rejected_count == before.rejected_count + 1   # reject unchanged
```

- [ ] **Step 2: Run — expect FAIL** (approve still increments at click).
- [ ] **Step 3: Implement** — in the approve handler, replace the `record_approval_decision(...)` call block (`:184-196`) with: compute `decision_type` (`"modified" if req and req.reason else "approved"`) and persist it onto the approval (`approval.artifact_refs = {**(approval.artifact_refs or {}), "decision_type": decision_type}`) — do NOT call `record_approval_decision`. Keep `await db.commit()`. Leave the reject handler exactly as-is.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -m "refactor(rebuild): stop click-time positive trust increment; persist decision_type on approval (Step 6C Task 5.1)"`.

---

### Task 5.2: Fire the positive increment on CONFIRMED verified outcome (inline + deferred)

**Files:**
- Modify: `src/services/dag_runner.py:499-503` (approved-resume path — capture the verdict, increment on CONFIRMED using the persisted `decision_type`)
- Modify: `src/services/scheduler/deferred_verification_tick.py:80-104` (eventual-consistency: read `decision_type` for user-approved steps; default `"approved"` for auto-exec — unchanged)
- Test: extend `tests/test_trust_increment_relocation.py`

- [ ] **Step 1: Write the failing test** — the increment fires only AFTER `_finalize_with_verification` returns CONFIRMED, and preserves `modified`:

```python
async def test_approved_write_increments_trust_only_on_confirmed(db, ...):
    # Drive an approved-resume step whose read-back verifies CONFIRMED.
    before = await get_trust_state(db, ws, "email.send", "high")
    await dag_runner.execute_step(run, step, ...)     # approved-resume, verifier → CONFIRMED
    after = await get_trust_state(db, ws, "email.send", "high")
    assert after.approved_count == before.approved_count + 1   # incremented at verified outcome


async def test_unverified_write_defers_increment_to_the_tick(db, ...):
    before = await get_trust_state(db, ws, "email.send", "high")
    await dag_runner.execute_step(run, step, ...)     # verifier → UNVERIFIED (completed_unverified)
    mid = await get_trust_state(db, ws, "email.send", "high")
    assert mid.approved_count == before.approved_count          # NOT yet
    await run_deferred_verification_tick(...)                   # later CONFIRMED
    after = await get_trust_state(db, ws, "email.send", "high")
    assert after.approved_count == before.approved_count + 1    # now incremented
```

- [ ] **Step 2: Run — expect FAIL** (no increment on the approved-resume path — it was moved out of routes but not yet added here).
- [ ] **Step 3: Implement** at `dag_runner.py:503` — capture the verdict and increment on CONFIRMED with the persisted `decision_type`:

```python
        risk = await self._trust_gate.assess_step_risk(capability, step, run)
        verdict = await self._finalize_with_verification(run, step, output, elapsed_ms, capability, risk)
        # Step 6C: the user-approval positive increment now fires HERE, on the confirmed
        # verified outcome (mirrors the auto-exec model at dag_runner.py:437), not at click.
        from src.services.verification import VerifyVerdict
        if verdict == VerifyVerdict.CONFIRMED:
            decision_type = await self._read_approval_decision_type(run, step)  # "approved"/"modified"
            await self._trust_gate.record_user_approval_outcome(
                capability, getattr(risk, "risk_level", risk), run.workspace_id or "", decision_type
            )
        # (completed_unverified defers to deferred_verification_tick — Step 4 below.)
```

Add `_read_approval_decision_type(run, step)` — selects the decided Approval for `(run_id, step_id)` and returns `artifact_refs.get("decision_type", "approved")`. Add `TrustGate.record_user_approval_outcome(capability, risk_level, ws, decision_type)` → `record_approval_decision(db, ws, capability, risk_level, decision_type)`.

- [ ] **Step 4: Update the deferred tick** (`deferred_verification_tick.py:80-104`) — when a `completed_unverified` user-approved step later CONFIRMS, read `decision_type` (persist it on the step meta at pause/resume so the tick can read it; default `"approved"` preserves the auto-exec path). Keep the SAVEPOINT wrapper.
- [ ] **Step 5: Run — expect PASS** (both tests). Run `tests/test_dag_runner*.py tests/test_deferred_verification*.py`.
- [ ] **Step 6: Commit** — `git commit -m "feat(rebuild): fire user-approval trust increment on CONFIRMED verified outcome, inline + deferred (Step 6C Task 5.2)"`.

---

# Phase 6 — Carry-forwards

### Task 6.1: CF-5 — validate before commit in `resume_deep_turn`

**Files:**
- Modify: `src/orchestrator/agent_invoker.py:394-409` (move `thread_id`/`agent_name`/agent-existence validation BEFORE the status commit)
- Test: `tests/test_resume_deep_turn_ordering.py` (new)

- [ ] **Step 1: Write the failing test** — a malformed approval (missing `thread_id`) is NOT consumed (stays `pending`, no status flip), so it isn't permanently stuck:

```python
async def test_malformed_approval_is_not_consumed_before_validation(db, invoker):
    appr = await seed_approval(db, artifact_refs={"tool_call_id": "tc1"})  # NO thread_id/agent_name
    frames = [f async for f in invoker.resume_deep_turn(
        approval_id=appr.approval_id, decision="approve", user_id=u, workspace_id=ws)]
    assert any(f.get("event") == "error" for f in frames)
    await db.refresh(appr)
    assert appr.status == "pending"   # NOT flipped to approved — re-resumable after a fix
```

- [ ] **Step 2: Run — expect FAIL** (status is flipped + committed before the `thread_id` check → ends `approved`).
- [ ] **Step 3: Implement** — reorder `resume_deep_turn`: after the ownership (`:386`) + pending (`:391`) guards, read `refs`, and validate `thread_id`/`agent_name` presence AND `self._agents.get(agent_name) is not None` **before** `approval.status = ...` / `db.commit()`. Only flip+commit once the rebuild inputs are known-good.
- [ ] **Step 4: Run — expect PASS** + `tests/test_deep_gate_*`.
- [ ] **Step 5: Commit** — `git commit -m "fix(rebuild): resume_deep_turn validates thread_id/agent before consuming the approval (Step 6C CF-5)"`.

---

### Task 6.2: CF-1 — persist + re-inject the ContextPack on resume

**Files:**
- Modify: `src/deep_runtime/middleware/trust_gate.py:150-159` (persist the assembled `context_block` on the Approval's `artifact_refs` at pause time)
- Modify: `src/orchestrator/agent_invoker.py:413` (resume rebuilds with the persisted context instead of `""`)
- Test: `tests/test_resume_context_reinjection.py` (new)

- [ ] **Step 1: Write the failing test** — the resumed agent's system prompt contains the original turn's context, not an empty block.
- [ ] **Step 2: Run — expect FAIL** (resume uses `build_system_prompt(agent, "")`).
- [ ] **Step 3: Implement** — at the live seam, thread the assembled `context_block` into `_build_deep_agent_for` so the gate can persist it on the Approval `artifact_refs["context_block"]` when it creates the Approval (cap the size; it's already stored JSONB). In `resume_deep_turn`, read `refs.get("context_block", "")` and pass it to `build_system_prompt(agent, context_block)` instead of `""`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -m "fix(rebuild): persist and re-inject ContextPack on deep resume (Step 6C CF-1)"`.

---

### Task 6.3: CF-2 — read the persisted verdict/risk instead of re-assessing on replay

**Files:**
- Modify: `src/deep_runtime/middleware/trust_gate.py:213-246` (on the replayed resume, if an Approval already exists for this `(thread_id, tool_call_id)`, skip `assess_risk` + `TrustEngine.evaluate` and use the persisted decision)
- Test: extend `tests/deep_runtime/test_trust_gate_idempotency.py`

- [ ] **Step 1: Write the failing test** — on resume, `assess_risk` is NOT called a second time (spy asserts call-count 1 across pause+resume).
- [ ] **Step 2: Run — expect FAIL** (assess runs again on replay).
- [ ] **Step 3: Implement** — early in the gated body, do the idempotent get-or-create FIRST; if an existing Approval is found (the replay case), read `risk_level`/`capability` off its `artifact_refs` and go straight to `interrupt()` (which immediately returns the resume value), bypassing the redundant `assess_risk` + `evaluate`. Preserve fail-closed behavior for the first pass (no existing row → assess as today).
- [ ] **Step 4: Run — expect PASS** + full deep-gate suite (the resume-executes-once / reject-blocks guards MUST still hold).
- [ ] **Step 5: Commit** — `git commit -m "perf(rebuild): deep gate reads persisted verdict on replay instead of re-assessing (Step 6C CF-2)"`.

---

### Task 6.4: CF-4 — checkpoint reaper for the durable saver

**Files:**
- Create: `src/deep_runtime/checkpoint_reaper.py` (`adelete_thread` on turn completion + a retention sweep)
- Modify: `src/orchestrator/agent_invoker.py` (call `adelete_thread(thread_id)` after a turn streams to completion **without** pausing — a paused turn keeps its checkpoint until resume)
- Modify: `src/services/scheduler/*` (register a periodic retention sweep — bound by age)
- Test: `tests/test_checkpoint_reaper.py` (real DB)

- [ ] **Step 1: Write the failing real-DB test** — after a completed (non-paused) deep turn, the thread's rows in `checkpoints`/`checkpoint_writes`/`checkpoint_blobs` are gone; a paused (interrupted) turn's rows survive until resume.
- [ ] **Step 2: Run — expect FAIL** (no reaper; rows accumulate).
- [ ] **Step 3: Implement** — a small helper `await saver.adelete_thread(thread_id)` invoked from the live seam when the stream finishes with no outstanding `__interrupt__`; a scheduler tick that deletes checkpoint rows older than a retention window (guard: never delete a thread with a still-pending Approval). Gate all of it on `runtime == "deep"` (the saver only exists then).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(rebuild): checkpoint reaper for the durable saver (durability=sync growth) (Step 6C CF-4)"`.

---

# Phase 7 — Holistic review + full gate

### Task 7.1: Full-suite gate

- [ ] `docker compose up -d postgres redis qdrant`
- [ ] `uv run pytest tests/ --ignore=tests/e2e` — expect green, count ≥ 3176 + the new tests. Investigate any skip that was previously a pass.
- [ ] `uv run alembic heads` — expect ONE `(head)` = the drop-operator rev. `uv run ruff check src/ tests/` clean.

### Task 7.2: Holistic opus review

- [ ] Dispatch one holistic reviewer (opus) over the whole 6C diff (`git diff main...HEAD` scoped to 6C commits). Charter: verify-don't-trust every claim; specifically re-derive (a) the write lock never spans the interrupt and uses identical keys cross-path; (b) no `operator` identity survives in `src/`; (c) the trust increment fires on CONFIRMED, never at click, and preserves `modified`; (d) the fast-path fence diverts writes; (e) each carry-forward's guard actually has teeth (negative controls present). Reproduce at least one guard by removing the fix and watching it fail.
- [ ] Address CRITICAL/HIGH findings; re-run the gate; commit fixes.

### Task 7.3: Docs + memory

- [ ] **CLAUDE.md** — this is a **durable architectural change** (unlike step-migration notes, which do NOT belong in CLAUDE.md): update the Agent Boundaries table (`Operator`→`Executor`, "scoped per step"), the "Only Operator executes external actions" line → "Only the Executor…", and the Capability-Based Routing table (write capabilities → Executor). Do NOT add step-by-step 6C migration notes.
- [ ] **Memory** — update `project_first_principles_rebuild.md` + `MEMORY.md` with the 6C outcome (commits, migrations, guards, carry-forward disposition). STOP before Step 7.

---

## Load-bearing guards (must have teeth — negative controls required)

| Guard | Test | Proves |
|---|---|---|
| Cross-path lock | `tests/test_write_lock_cross_path.py` | deep + autonomous keys collide for same `(ws, cap)`; different caps don't (negative control). |
| Read never locks | `tests/deep_runtime/test_write_lock_middleware.py`, `tests/test_step_runner_write_lock.py` | reads bypass the lock on BOTH paths. |
| CF-3 fence | `tests/test_approval_idempotency_constraint.py` | duplicate `(ws, thread_id, tool_call_id)` → `IntegrityError`. |
| Per-step scope | `tests/test_step_runner_scope.py` | a step is offered ONLY its capability's tools (no cross-capability leak — negative control). |
| No operator survives | `tests/test_executor_agent.py` + Reviewer A grep | `AGENTS.get("operator") is None`; writes route to `executor`. |
| Trust on verified | `tests/test_trust_increment_relocation.py` | increment fires on CONFIRMED, NOT at click; reject still at click; `modified` preserved; deferred case covered. |
| Fast-path fence | `tests/test_fast_path_no_write.py` | no fast intent writes today; a write-emitting one is diverted off the ungated path. |
| CF-5 ordering | `tests/test_resume_deep_turn_ordering.py` | malformed approval not consumed before validation (stays `pending`). |
| CF-4 reaper | `tests/test_checkpoint_reaper.py` | completed turn's checkpoints deleted; paused turn's survive. |

---

## Self-review (run by the plan author before handoff)

- **Spec coverage:** (1) write lock → Phase 1; (2) kill Operator → Phase 3; (3) fast-path → Phase 4; (4) trust relocation → Phase 5; carry-forwards CF-1/2/3/4/5 → Phases 2 + 6. CF-STEP5 explicitly excluded (Fork D). Gate stays dormant (Fork A) — no HTTP/CoreEvent/frontend tasks. ✅
- **Placeholder scan:** every code step carries real code or an exact change-spec with file:line; test-retarget (3.6) enumerates the breaking assertions rather than reproducing 26 files (justified — mechanical rename, intent unchanged). ✅
- **Type consistency:** `write_lock_key`/`acquire_write_lock`/`WriteLockContended` (1.1) reused verbatim in 1.2/1.3/1.4; `make_write_lock_middleware`/`make_lock_wrapped_execute_tool_fn` names consistent; `record_user_approval_outcome`/`_read_approval_decision_type` defined in 5.2 and used nowhere earlier; `build_executor_tools(step_capability)` signature consistent between 3.3 definition and its `run_step_via_agent_loop` caller. ✅
- **Migration count:** two (2.1 columns+UNIQUE, 3.5 drop-row), chained `c7d3e4f5a6b8 → 2.1 → 3.5`, single owner each. ✅

---

## Execution handoff

Plan complete. **Two execution options:**
1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, spike-first, opus holistic at the end. This matches the rhythm proven across Steps 0–6B.
2. **Inline Execution** — batch tasks in-session with checkpoints (superpowers:executing-plans).

Execution is the **next** session — this session STOPS at the committed plan.
