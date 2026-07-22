# Spike: `AsyncPostgresSaver` durable resume + non-pickle serializer

**Date:** 2026-06-28 (spec) · **Ran:** 2026-07-01 · **Task:** Step-1 rebuild, Task 7
**Status:** ✅ **RESOLVED — ran green 2026-07-01.** Executed for real against live
Postgres + `langgraph-checkpoint-postgres 3.1.0`. NOT faked.

## The spike question (answered)
1. Does `AsyncPostgresSaver` provide durable resume — after a worker is killed
   mid-run, does resuming the same `thread_id` continue the run? **Yes.**
2. Is durability **at-least-once** (the interrupted node REPLAYS from the top on
   resume), confirming the spec's premise that **the Step-1 idempotency ledger is
   what makes the mandatory replay safe**? **Yes — the node ran twice.**
3. Can a **non-pickle serializer** be pinned so checkpoint payloads are never
   `pickle` (per "treat checkpoint payloads as untrusted")? **Yes — the default
   serde is `JsonPlusSerializer` (msgpack), and the persisted checkpoint blobs
   contain no pickle streams.**

## The probe
`backend/spikes/postgres_saver/probe.py` (package `backend/spikes/postgres_saver/`).
Self-contained and **re-runnable** — seeds its own `User` + `Workspace` FK chain
and a dedicated `spike_effects` table, and tears everything down (plus its
checkpoint rows) in a `finally` block, even on failure. Ran twice back-to-back
with zero residue.

What it does:
- Builds a minimal LangGraph `StateGraph` with **one node** guarded by the
  **real** `IdempotencyLedger(factory).reserve(...)` using a **fixed** semantic
  `identity_key` (`{ws}:s1:email.send:sem:spike`) that is stable across resume.
- On the first pass (`already_done` is False): INSERT one row into `spike_effects`
  (the Postgres-backed "external API call"), then `ledger.record_success(...)`,
  then `raise RuntimeError("crash before checkpoint")` — simulating a kill *after*
  the effect + ledger record but *before* LangGraph checkpoints the node.
- On resume: the ledger row is now `completed`, so `reserve` returns
  `already_done=True`, the node **skips** the effect and returns cleanly. The
  ledger state itself distinguishes first-pass from resume — no manual flag.
- Asserts `select count(*) from spike_effects == 1` (exactly once across
  crash + resume) and that the node body ran twice (replay evidence).

### Run command
```bash
cd backend
uv run python -m spikes.postgres_saver.probe
```

### Observed output (2026-07-01, live Postgres)
```
SERDE=JsonPlusSerializer NON_PICKLE=True
[pass 1] invoking (durability='sync') — expecting crash
  [node] execution #1 (thread=run_01KWDKSP9FDCP5QTC3JN1X8CB6)
  [node] inserted spike_effects row (external effect fired)
  [node] ledger.record_success -> completed
[pass 1] caught expected crash: crash before checkpoint
[pass 2] resuming same thread_id (input=None)
  [node] execution #2 (thread=run_01KWDKSP9FDCP5QTC3JN1X8CB6)
  [node] ledger already_done -> SKIP effect (result={'status': 'sent'})
[pass 2] completed: result.get('passes')=1
[serde] round_trip_ok=True encoded_type='msgpack'
[assert] spike_effects count=1 node_executions=2
[checkpoints] 3 blob rows, types={'msgpack'}, any_pickle=False
============================================================
REPLAYED_ON_RESUME=True
EXACTLY_ONCE=True
SERDE=JsonPlusSerializer NON_PICKLE=True
CHECKPOINT_ROWS_NOT_PICKLE=True
============================================================
```
Exit code `0`.

## Findings / API facts (as-run, `langgraph-checkpoint-postgres 3.1.0`)
- **At-least-once replay is real.** `execution #1` (first pass) and `execution #2`
  (resume) both ran the node body — the interrupted node replayed from the top.
  The effect count stayed at **1** because the ledger dedups the replay.
- **Durability mode + how it's passed:** `durability="sync"` is an **`ainvoke`
  kwarg** in this version — `await graph.ainvoke(input, cfg, durability="sync")`.
  It is *not* a `compile()` argument here (`StateGraph.compile` has no `durability`
  param in 1.2.6). The version's `CompiledStateGraph.ainvoke` signature exposes
  `durability: Durability | None = None`.
- **Resume call:** `await graph.ainvoke(None, cfg, durability="sync")` — re-invoking
  the **same `thread_id`** with `input=None` picks up the pending task for the
  interrupted node and replays it. (Re-passing the original input is not needed;
  `None` resumes from the persisted checkpoint.)
- **Serializer:** the checkpointer's default `saver.serde` is
  `JsonPlusSerializer` — **not** pickle. `dumps_typed`/`loads_typed` round-tripped
  a nested dict, and the encoded type was `msgpack` (JsonPlus uses ormsgpack/json,
  never pickle). The persisted `checkpoint_blobs` rows are all `type='msgpack'`
  and none begins with the pickle PROTO opcode (`b'\x80'`). No custom serde had to
  be constructed — the default already satisfies the "no pickle" requirement, and
  it can be overridden explicitly via
  `AsyncPostgresSaver.from_conn_string(uri, serde=...)` if ever needed.
- **Connection string (psycopg3):** `AsyncPostgresSaver` uses **psycopg3**, so its
  DSN is `postgresql://jarvis:jarvis@localhost:5432/jarvis` — the app's
  `+asyncpg` suffix is stripped (`SQLA_URL.replace("+asyncpg", "", 1)`). The real
  `IdempotencyLedger` continues to use the app's SQLAlchemy/asyncpg sessionmaker;
  the two connection systems hit the same Postgres, which is fine.
- **`saver.setup()`** must be called once to create the checkpoint tables
  (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`).

## Conclusion (the decision gate)
✅ Resume continues the run and the killed node **re-runs from the top** — replay
semantics confirmed.
✅ With the real per-`(workspace, identity_key)` idempotency ledger in place, the
external write fires **exactly once** across the kill + resume.
✅ A **non-pickle serializer** (`JsonPlusSerializer`, msgpack) round-trips
checkpoint state and no pickle blobs are persisted.

**The ledger makes LangGraph's at-least-once replay exactly-once.** This is the
hard **Step-1 prerequisite** for the autonomous durable cutover (**Step 10**), and
it is now satisfied.

## Still pending for Step 10 (not this spike)
The multi-tenant isolation test — a thread/tool call in workspace A cannot read
workspace B's data through the `Store`/checkpointer — is a **Step-10 deliverable**,
not part of this spike. The substrate's namespace isolation is fail-open, so the
enforcement wrapper (bind `workspace_id` into `thread_id` + `Store` namespace
prefix) and its blocking test must land before the autonomous path uses the
checkpointer.

## Downstream
Feeds **Step 1** (idempotency ledger acceptance) and **Step 10** (autonomous
durable cutover). This spike is green; the ledger's exactly-once gate is proven.
The probe (`backend/spikes/postgres_saver/probe.py`) is re-runnable at any time.
