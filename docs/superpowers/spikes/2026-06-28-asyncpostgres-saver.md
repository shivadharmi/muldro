# Spike: `AsyncPostgresSaver` durable resume + non-pickle serializer

**Date:** 2026-06-28 · **Task:** Step-0 rebuild, Task 7
**Status:** ⛔ **BLOCKED — pending infra (no dev Postgres in this environment).** Not run; NOT faked.

## Why blocked
The probe requires a running Postgres (the spike proves durable resume against a real
checkpointer store). This environment has no Postgres: `pg_isready` → not ready, and there is no
`docker compose` postgres service running. The result must come from an actual kill-and-resume run,
so it is deferred — not estimated.

Additionally: `langgraph-checkpoint-postgres` is **not installed** today
(`import langgraph.checkpoint.postgres` → `ModuleNotFoundError`). The dependency add is scheduled for
**Step 1** (where the idempotency ledger first needs it), so this spike runs as part of / just before
Step 1, against the dev Postgres.

## The spike question (to answer when infra is available)
1. Does `AsyncPostgresSaver` provide durable resume: after a worker is killed mid-run, does resuming
   the same `thread_id` continue from the last checkpoint?
2. Is durability **at-least-once** (replay re-runs the interrupted node from the top), confirming the
   spec's premise that **the Step-1 idempotency ledger is what makes the mandatory replay safe**?
3. Can a **non-pickle serializer** (JSON/msgpack) be pinned for the checkpoint store (no `pickle`,
   per the spec's "treat checkpoint payloads as untrusted")?

## Probe to run (when Postgres is available)
Create `backend/spikes/postgres_saver/probe.py`:
- Build a tiny LangGraph graph with one node that performs a **recorded external side effect**
  (append to a row / increment a counter) guarded by a per-`(thread_id)` idempotency check.
- Run it under `AsyncPostgresSaver` using `JARVIS_DATABASE_URL`, with `durability="sync"`.
- Kill mid-run: raise **after** the side effect's "API call" but **before** the checkpoint commits.
- Resume the same `thread_id`; assert the external effect fired **exactly once**.
- Configure and round-trip a non-pickle serializer; assert state survives.

Run:
```bash
docker compose up -d            # bring up dev Postgres
cd backend && source .venv/bin/activate
pip install -e .                # after langgraph-checkpoint-postgres is added (Step 1)
python -m spikes.postgres_saver.probe
```

## Acceptance criteria (the decision gate)
- ✅ Resume continues the run and the killed node **re-runs from the top** (confirms replay semantics).
- ✅ With the per-`(workspace, step)` idempotency ledger in place, the external write fires **exactly
  once** across the kill+resume (this is the **hard Step-1 gate before any autonomous cutover**).
- ✅ A **non-pickle serializer** round-trips checkpoint state.

## Also covered by this spike (multi-tenant isolation, spec §4.10)
When the checkpointer is live, add the **blocking isolation test**: a tool call / thread in workspace
A cannot read workspace B's data through the `Store`/checkpointer. The substrate's namespace isolation
is fail-open, so the enforcement wrapper (bind `workspace_id` into `thread_id` + `Store` namespace
prefix) must be in place and this test must pass before the autonomous path uses the checkpointer.

## Downstream
Feeds **Step 1** (idempotency ledger acceptance test) and **Step 10** (autonomous durable cutover).
Until this runs green, Step 10 must not ship (the spec marks the ledger a hard prerequisite).
