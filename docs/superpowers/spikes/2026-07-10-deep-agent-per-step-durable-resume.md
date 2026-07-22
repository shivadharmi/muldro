# Spike: per-step `build_deep_agent` durable resume + ledger exactly-once (Step 10C Phase 0.1, SQ1)

**Date:** 2026-07-10
**Probe:** `backend/spikes/deep_autonomous/probe_per_step_durable.py`
**Run:** `uv run python -m spikes.deep_autonomous.probe_per_step_durable` (exit 0 on CONFIRMED)
**Status:** CONFIRMED — SQ1 stays on **Branch A** (autonomous executor on `build_deep_agent`).

---

## Question (the plan-killer)

Does a **real `build_deep_agent` react loop**, per single step, compiled under a **real
`AsyncPostgresSaver`** with `durability="sync"` on a `make_thread_id(workspace_id)` thread,
that is **killed mid-tool-call** and then **resumed on the same `thread_id` via
`ainvoke(None, cfg, durability="sync")`**, fire its external write **EXACTLY ONCE** — because
the **real** `IdempotencyLedger` (via `make_idempotent_execute_tool_fn`) dedups LangGraph's
mandatory at-least-once replay?

If NO → SQ1 falls to Branch B (a `dag_runner` rewrite = a different plan).

## What the probe exercises (real, not faked)

- Real `build_deep_agent` (capability-scope guard auto-installed, fail-closed write guard
  satisfied, `create_deep_agent` compile) — only `build_chat_model` is monkeypatched to a
  deterministic fake `BaseChatModel` so the loop is offline/reproducible (no API).
- Real `jarvis_tool_dispatcher` (`make_jarvis_tool_dispatcher`) wired via a
  positional→keyword adapter to the real `make_idempotent_execute_tool_fn` over a real
  `IdempotencyLedger` and a real Postgres effect (`spike_effects` INSERT).
- Real `AsyncPostgresSaver` built by `build_async_postgres_saver` (the 10C/B9 reuse target).
- Real `make_thread_id` / `workspace_of_thread_id`.
- Fresh agent + fresh ledger wrapper rebuilt for the resume pass (faithful process restart).

## Crash model used = **MODEL 1** (target / preferred)

The write effect fires **and** `ledger.record_success` commits, THEN the dispatcher adapter
raises a hard `RuntimeError` on the first pass only, simulating a kill AFTER the effect +
record but BEFORE the tool-node checkpoint. On resume the ledger row is `completed` →
`already_done` → the effect is NOT re-fired → the tool node returns cleanly → the agent
completes.

**Why Model 1 and not Model 3:** an offline pre-flight established that the deep agent's
ToolNode does **not** swallow a `RuntimeError` raised from a `wrap_tool_call` middleware into
a `ToolMessage(status="error")` — it propagates as a **node failure**, leaving a resumable
checkpoint that `ainvoke(None, cfg)` replays. (All of `RuntimeError`, a `BaseException`
subclass, and `asyncio.CancelledError` behaved identically: pass-1 raised, resume re-ran the
tool node exactly once.) So clean-resume Model 1 holds; the weaker Model 3
(crash-before-record → `in_flight_conflict` → fail-closed no re-fire) was not needed.

## Result (probe output, verbatim)

```
SERDE=JsonPlusSerializer NON_PICKLE_SERDE=True
[pass 1] build_deep_agent + AsyncPostgresSaver — ainvoke(durability='sync')
  [effect] fired external write (spike_effects INSERT) tool=spike_write_email
  [crash] raising RuntimeError AFTER effect+record (pre-checkpoint kill)
[pass 1] caught expected crash: spike: simulated process kill after effect+record
[pass 2] FRESH build_deep_agent, resume same thread_id — ainvoke(None, cfg)
[pass 2] resumed cleanly; final message count=4
[assert] spike_effects=1 dispatch_after_pass1=1 total_dispatch=2 effects_fired=1
[assert] idempotency_ledger rows for ws = ['completed']
[assert] workspace_of_thread_id('c:ws_...:...') == 'ws_...' -> True
[checkpoints] 3 blob rows, types={'msgpack'}, any_pickle=False
  [readonly] read bypassed ledger (inner called, reserve NOT called) = True
[SQ4] gated chain (capability_scope + ledger dispatcher + trust_gate[AUTONOMOUS]) compiled with checkpointer -> True
================================================================
CRASH_MODEL=1 (kill after effect+record, before checkpoint; clean resume)
EXACTLY_ONCE=True
REPLAYED_ON_RESUME=True
WS_BOUND_THREAD_ID=True
CHECKPOINT_ROWS_NOT_PICKLE=True
READONLY_BYPASSES_LEDGER=True
SQ4_AUTONOMOUS_COMPOSES_WITH_CHECKPOINTER=True
SERDE=JsonPlusSerializer
================================================================
RESULT=CONFIRMED
```

`total_dispatch=2` with `effects_fired=1` is the non-vacuous core: the tool node genuinely
**replayed** on resume, yet the external effect fired **exactly once**.

## Caveats / carry-forward for P1

1. **`identity_key` ordinal is a non-issue for semantic-keyed writes.** `email.send` has an
   `IdentitySpec(identity_fields=to,cc,bcc,subject)` → the key is `...:sem:<digest>`, which is
   **ordinal-independent**. The per-wrapper `count()` ordinal only matters for the POSITIONAL
   fallback (`...:pos:<ordinal>`). For positional-keyed write capabilities, resume MUST rebuild
   a fresh wrapper (ordinal restarts at 0) AND the step must re-issue its writes in the SAME
   ORDER on replay — a genuine restart already gives the fresh wrapper; the fake model here
   also re-issues the same single tool call, so ordinal parity was trivially satisfied. P1 must
   preserve deterministic write ordering for positional-keyed caps.
2. **Semantic-key stability across replay is what makes clean-resume work**, not the wrapper
   instance. The digest is over normalized identity fields, so an LLM-recomposed body does not
   change the key. A real (non-fake) model that regenerates *subject/recipients* on replay WOULD
   change the key and could double-fire — but on a true crash+resume LangGraph replays the
   **persisted** tool call (same args), so this is safe for the resume path.
3. **A raised exception from the dispatcher propagates as a node failure** and leaves a pending
   task. This is exactly the durability the ledger needs. It also means any *unhandled* error in
   a gate/dispatcher middleware fails the whole `ainvoke` (correct: fail-closed), so P1's
   autonomous driver must treat an `ainvoke` raise as "resume this thread", not "abort the run".
4. **`build_async_postgres_saver` reuse confirmed**: `SERDE=JsonPlusSerializer`, blobs
   `type='msgpack'`, none pickle. The autonomous durable checkpointer can reuse this builder and
   the `make_thread_id` identity as planned (B9).
5. **Resume works with a FRESH compiled graph** on the same `thread_id` (not just the same
   in-memory graph object the Step-1 probe reused). This is the real process-restart shape and it
   held — LangGraph keys resume by thread_id + node topology, and an identically-built deep agent
   reproduces the topology.
6. **SQ4 is compile-only here.** The gated chain with `authorization_source=AUTONOMOUS` COMPILES
   with the checkpointer; the probe does not *invoke* the gate (an active `trust_gate` on
   `AUTONOMOUS` would `interrupt()` for approval and never fire the effect, which is a separate
   already-proven interrupt/resume path — see `2026-06-28-interrupt-in-wrap-tool-call.md`). P1
   wiring of the live autonomous gate + interrupt round-trip is a distinct integration step.

---

DECISION: per-step build_deep_agent durable resume + ledger exactly-once = CONFIRMED; ws-bound thread_id = CONFIRMED; SQ1 -> Branch A; SQ4 (authorization_source=AUTONOMOUS composes with checkpointer) = CONFIRMED.
