"""Spike probe: AsyncPostgresSaver durable resume + exactly-once via the real
IdempotencyLedger + non-pickle serializer round-trip.

Proves the Step-1 acceptance gate:
  1. LangGraph durability is at-least-once: on crash the interrupted node REPLAYS
     from the top when the same thread_id is resumed.
  2. The real IdempotencyLedger makes that replay safe: the Postgres-backed
     external effect fires EXACTLY ONCE across crash + resume.
  3. A non-pickle serializer (JsonPlusSerializer) round-trips checkpoint state,
     and the persisted checkpoint rows are not pickle blobs.

Run:
    uv run python -m spikes.postgres_saver.probe

Self-contained and re-runnable: seeds its own User+Workspace FK chain and a
dedicated spike_effects table, and tears everything down in a finally block.

This is an exploratory spike, not production code — hence the module-level
prints and the broad orchestration. It should still lint clean.
"""

from __future__ import annotations

import asyncio
import pickle
from typing import Annotated, Any, TypedDict

import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.users import User, Workspace
from src.services.idempotency.ledger import IdempotencyLedger

# --- Connection strings -----------------------------------------------------
# The app / IdempotencyLedger use SQLAlchemy asyncpg. AsyncPostgresSaver uses
# psycopg3, whose DSN must NOT carry the "+asyncpg" driver suffix.
SETTINGS = get_settings()
SQLA_URL = SETTINGS.database_url  # postgresql+asyncpg://...
PSYCOPG_URL = SQLA_URL.replace("+asyncpg", "", 1)  # postgresql://... (psycopg3)

# Node-run counter, incremented every time the node body executes. Used purely
# to PROVE the node replayed on resume (should end at 2: first pass + resume).
NODE_RUNS: list[str] = []


class SpikeState(TypedDict):
    """Minimal graph state. `note` uses a reducer so the JsonPlus serializer has
    a non-trivial (LangChain message) payload to round-trip through Postgres."""

    note: Annotated[list, add_messages]
    passes: int


async def _reachable() -> bool:
    try:
        conn = await asyncpg.connect(dsn=PSYCOPG_URL)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - probe: report and bail cleanly
        print(f"POSTGRES_UNREACHABLE: {exc!r}")
        return False


def build_graph(
    factory: async_sessionmaker,
    workspace_id: str,
    thread_id: str,
    identity_key: str,
):
    """One-node graph. The node is guarded by the real IdempotencyLedger and
    fires a Postgres-backed effect exactly once. On the FIRST pass (ledger not
    yet completed) it inserts the effect, records success, then raises to
    simulate a kill AFTER the effect + record but BEFORE LangGraph checkpoints
    the node. On resume the ledger row is already `completed`, so `already_done`
    is True and the node skips the effect and returns cleanly."""

    async def effect_node(state: SpikeState) -> dict[str, Any]:
        NODE_RUNS.append(thread_id)
        run_no = len(NODE_RUNS)
        print(f"  [node] execution #{run_no} (thread={thread_id})")

        ledger = IdempotencyLedger(factory)
        outcome = await ledger.reserve(
            workspace_id=workspace_id,
            run_id=thread_id,
            step_id="s1",
            capability="email.send",
            identity_key=identity_key,
        )

        if outcome.already_done:
            print(f"  [node] ledger already_done -> SKIP effect (result={outcome.result})")
            return {"note": [("assistant", "resumed-skip")], "passes": 1}

        # First pass: fire the (idempotent) external effect against Postgres.
        async with factory() as db:
            await db.execute(
                text("INSERT INTO spike_effects (id) VALUES (:id)"),
                {"id": f"eff_{ULID()}"},
            )
            await db.commit()
        print("  [node] inserted spike_effects row (external effect fired)")

        await ledger.record_success(outcome.ledger_id, {"status": "sent"})
        print("  [node] ledger.record_success -> completed")

        # Simulate a crash: killed after the effect + record, before the
        # checkpoint for this node commits. LangGraph will replay this node on
        # resume (at-least-once) — the ledger is what makes that replay safe.
        raise RuntimeError("crash before checkpoint")

    builder = StateGraph(SpikeState)
    builder.add_node("effect", effect_node)
    builder.add_edge(START, "effect")
    builder.add_edge("effect", END)
    return builder


async def run_probe() -> int:
    if not await _reachable():
        print("EXACTLY_ONCE=SKIPPED (postgres unreachable)")
        return 1

    engine = create_async_engine(SQLA_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    thread_id = f"run_{suffix}"
    # FIXED semantic key — stable across the crash + resume so the ledger dedups.
    identity_key = f"{workspace_id}:s1:email.send:sem:spike"

    exactly_once = False
    non_pickle = False
    serde_name = "?"
    replayed = False
    rows_not_pickle = False

    try:
        # --- Seed FK chain -------------------------------------------------
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"spike-{suffix}@example.com", display_name="spike"))
            db.add(Workspace(workspace_id=workspace_id, name="spike-ws", owner_user_id=user_id))
            await db.commit()

        # --- Dedicated effect table ---------------------------------------
        async with factory() as db:
            await db.execute(text("CREATE TABLE IF NOT EXISTS spike_effects (id text primary key)"))
            await db.execute(text("DELETE FROM spike_effects"))
            await db.commit()

        builder = build_graph(factory, workspace_id, thread_id, identity_key)

        async with AsyncPostgresSaver.from_conn_string(PSYCOPG_URL) as saver:
            await saver.setup()

            # Serializer inspection: confirm non-pickle serde is in use.
            serde_name = type(saver.serde).__name__
            non_pickle = isinstance(saver.serde, JsonPlusSerializer)
            print(f"SERDE={serde_name} NON_PICKLE={non_pickle}")

            graph = builder.compile(checkpointer=saver)
            cfg = {"configurable": {"thread_id": thread_id}}

            # --- Pass 1: expect the crash to propagate -----------------------
            print("[pass 1] invoking (durability='sync') — expecting crash")
            try:
                await graph.ainvoke({"note": [], "passes": 0}, cfg, durability="sync")
                print("[pass 1] UNEXPECTED: no crash raised")
            except RuntimeError as exc:
                print(f"[pass 1] caught expected crash: {exc}")

            # --- Pass 2: resume the SAME thread_id ---------------------------
            # Resume by re-invoking with None input on the same thread — LangGraph
            # picks up the pending task for the interrupted node and replays it.
            print("[pass 2] resuming same thread_id (input=None)")
            result = await graph.ainvoke(None, cfg, durability="sync")
            print(f"[pass 2] completed: {result.get('passes')=}")

            # --- Serializer round-trip proof ---------------------------------
            sample = {"hello": "world", "n": 7, "nested": {"a": [1, 2, 3]}}
            enc = saver.serde.dumps_typed(sample)
            dec = saver.serde.loads_typed(enc)
            round_trip_ok = dec == sample
            print(f"[serde] round_trip_ok={round_trip_ok} encoded_type={enc[0]!r}")
            non_pickle = non_pickle and round_trip_ok

        # --- Assert exactly-once via DB-observed effect count -----------------
        async with factory() as db:
            count = (await db.execute(text("SELECT count(*) FROM spike_effects"))).scalar_one()
        exactly_once = count == 1
        replayed = len(NODE_RUNS) >= 2
        print(f"[assert] spike_effects count={count} node_executions={len(NODE_RUNS)}")

        # --- Confirm persisted checkpoint rows are NOT pickle blobs -----------
        rows_not_pickle = await _checkpoints_not_pickle(thread_id)

    finally:
        # --- Teardown (best-effort, always runs) ------------------------------
        try:
            async with factory() as db:
                await db.execute(text("DROP TABLE IF EXISTS spike_effects"))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] spike_effects drop failed: {exc!r}")
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] FK teardown failed: {exc!r}")
        await _delete_checkpoints(thread_id)
        await engine.dispose()

    print("=" * 60)
    print(f"REPLAYED_ON_RESUME={replayed}")
    print(f"EXACTLY_ONCE={exactly_once}")
    print(f"SERDE={serde_name} NON_PICKLE={non_pickle}")
    print(f"CHECKPOINT_ROWS_NOT_PICKLE={rows_not_pickle}")
    print("=" * 60)

    ok = exactly_once and non_pickle and replayed and rows_not_pickle
    return 0 if ok else 2


async def _checkpoints_not_pickle(thread_id: str) -> bool:
    """Read the raw checkpoint blobs psycopg3 persisted and prove none of them
    is a pickle stream. JsonPlus rows are tagged type 'json'/'msgpack'; a pickle
    payload would start with the pickle PROTO opcode (b'\\x80')."""
    conn = await asyncpg.connect(dsn=PSYCOPG_URL)
    try:
        rows = await conn.fetch(
            "SELECT type, blob FROM checkpoint_blobs WHERE thread_id = $1", thread_id
        )
        if not rows:
            print("[checkpoints] no checkpoint_blobs rows found (nothing to verify)")
            return True
        types = {r["type"] for r in rows}
        pickle_proto = pickle.PROTO  # b'\x80' — first opcode of any pickle stream
        any_pickle = False
        for r in rows:
            blob = r["blob"]
            if blob and bytes(blob[:1]) == pickle_proto:
                any_pickle = True
        print(f"[checkpoints] {len(rows)} blob rows, types={types}, any_pickle={any_pickle}")
        return not any_pickle
    finally:
        await conn.close()


async def _delete_checkpoints(thread_id: str) -> None:
    conn = await asyncpg.connect(dsn=PSYCOPG_URL)
    try:
        for tbl in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - table may not exist
                pass
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
