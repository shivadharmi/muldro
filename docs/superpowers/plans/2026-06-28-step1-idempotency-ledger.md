# Step 1 — Per-Step Idempotency Ledger + Semantic Identity Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-step / per-tool idempotency ledger keyed on a **per-capability semantic identity key captured at first-attempt time** (not a hash of full args), wired into the autonomous execution path so a killed-and-resumed write fires its external effect **exactly once** even when the LLM recomposes the raw args on resume.

**Architecture:** A new `src/services/idempotency/` package: (1) an **identity registry** that derives a stable semantic key per capability (semantic fields / provider-native token / positional fallback), (2) an **`IdempotencyLedger`** service backed by a new workspace-scoped `idempotency_ledger` table with a `(workspace_id, identity_key)` unique index (the DB constraint is the authoritative exactly-once gate, following the existing `NormalizedEvent`/`Plan`/`TaskRun` idempotency idiom), and (3) a **DI wrapper** `make_idempotent_execute_tool_fn` that wraps the injected `execute_tool_fn` at the one autonomous-path seam (`step_runner.run_step_via_agent_loop`). `agent_loop` and the by-design-ungated chat path are **untouched**. `langgraph-checkpoint-postgres` is added to deps here; the `AsyncPostgresSaver` durable-resume kill/exactly-once spike is written **ready-to-run** but marked **blocked-pending-infra** in this environment (no Postgres + langgraph not installed) — never faked.

**Tech Stack:** Python 3.12/3.13, pytest (async via root `conftest.py` `pytest_pyfunc_call`), SQLAlchemy 2 / asyncpg, Pydantic v2, ruff, alembic. (LangGraph/deepagents are declared in `pyproject.toml` but **not installed** in this venv — see Infra note.)

**Source spec:** [`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`](../specs/2026-06-28-first-principles-rebuild-design.md) §6 Step 1, §4.8 (data model / per-step idempotency), §7 (double-fire-on-resume risk).

**Depends on:** Step 0 (`7b5a4fc`) — the green baseline this plan builds on.

---

## Infra note (verified 2026-07-01 in this environment)

- **No Postgres, no Docker** (`pg_isready` absent; Docker daemon not running).
- **`langgraph` / `langchain` / `deepagents` / `langchain-anthropic` are NOT installed** in `backend/.venv` (only `langchain-core` + `anthropic` are). `pyproject.toml` **declares** them (`langgraph>=1.2,<2`, `deepagents>=0.6.11,<0.7`, …) but the venv is lean.
- **Consequence:** the full suite collects **3003 tests** and runs, **except** the 5 `tests/deep_runtime/` files (they import `langgraph`/`langchain` and error on collection). **Do not** add `tests/deep_runtime/` to any Step-1 command; scope pytest to the files this plan creates + the non-deep_runtime suite.
- **Everything in this plan except Task 7's spike is runnable here** (compiled-SQL + mocked-session tests, per the codebase's no-live-DB test convention). Task 7 (the `AsyncPostgresSaver` kill/resume spike) is **blocked-pending-infra**: it needs Postgres **and** a langgraph-checkpoint install. It ships a ready-to-run probe + honest finding doc, matching the Step-0 spike precedent. **Never fake spike results.**

**Run all commands from `backend/` with the venv active:** `cd backend && source .venv/bin/activate`.

**Pre-flight (run once before starting):**
```bash
cd backend && source .venv/bin/activate && pytest tests/ -q --ignore=tests/deep_runtime -p no:cacheprovider 2>&1 | tail -5
```
Expected: PASS (establishes the green baseline; `tests/deep_runtime` ignored because langgraph is uninstalled here).

---

## Why a full-arg hash is wrong (the core design constraint)

Every existing idempotency key in the codebase is **content-derived** and that is fine for its owner:
- `NormalizedEvent`: `source:entity_id[:message_id|last_edited_time]:event_type` (content fixed at ingest).
- `Plan`: `user:{sha256(goal)[:16]}` / `perception:{source}:{sha256(goal)[:16]}` (goal fixed at plan time).

A **write step is different**: on resume the durable engine replays the host node, the LLM **recomposes the tool args** (a regenerated email body), and a full-arg hash therefore **changes** → the DB sees a "new" key → the write **fires twice** — on exactly the irreversible writes the ledger exists to protect (spec §7, first row). The opposite failure is **over-normalization** that collapses two genuinely-distinct writes into one (the second is silently dropped).

The **semantic identity key** threads this: it is derived from the *identity-defining* fields (recipients + subject), **excludes the volatile fields** (body), and is **scoped to the execution position** (`run_id:step_id:capability:ordinal`) so it is stable across resume yet distinct per logical write. Where a provider offers a native idempotency token, that token *is* the identity (the provider dedupes).

---

## File Structure

**Create:**
- `backend/src/services/idempotency/__init__.py` — package exports.
- `backend/src/services/idempotency/identity.py` — `IdentitySpec`, `IDENTITY_SPECS` registry, `derive_identity_key(...)`, `validate_identity_coverage(...)`. Pure, no DB.
- `backend/src/services/idempotency/ledger.py` — `IdempotencyLedger` (reserve / record_success / mark_failed) + pure statement-builders (`_ledger_lookup_stmt`) for compiled-SQL tests + `ReserveOutcome`.
- `backend/src/services/idempotency/wrapper.py` — `make_idempotent_execute_tool_fn(...)` (the DI seam) + `IdempotencyContext`.
- `backend/src/models/idempotency_ledger.py` — `IdempotencyLedgerEntry` model (`idempotency_ledger` table).
- `backend/alembic/versions/a2f5c9d18b47_step_idempotency_ledger.py` — migration (create table + unique index).
- `backend/spikes/postgres_saver/probe.py` — throwaway, ready-to-run `AsyncPostgresSaver` durable-resume probe (Task 7).
- Tests: `backend/tests/idempotency/__init__.py`, `test_identity_key.py`, `test_ledger_schema.py`, `test_ledger_sql.py`, `test_ledger_service.py`, `test_idempotent_wrapper.py`.

**Modify:**
- `backend/src/models/__init__.py` — import + `__all__` the new model (so `Base.metadata` + autogenerate see it).
- `backend/src/models/ids.py` — register the `idem` ULID prefix.
- `backend/src/services/step_runner.py` — wrap `self._execute_tool_fn` with `make_idempotent_execute_tool_fn` inside `run_step_via_agent_loop` before the `agent_loop(...)` call.
- `backend/pyproject.toml` — add `langgraph-checkpoint-postgres`.
- `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md` — update status (dep landed; still blocked-pending-infra with the new langgraph-not-installed finding).

**Untouched (by design):** `agent_loop.py` (the ledger is injected through the existing `execute_tool_fn` seam); the chat path (`jarvis.process_message`/`process_message_stream` inject a plain `execute_tool_fn` → ledger is a no-op there).

---

## Task 1: Add `langgraph-checkpoint-postgres` dependency + record the infra reality

`langgraph-checkpoint-postgres` is the named execution-truth owner for Step 10. The spec lands the dependency in Step 1. In this venv the *install* cannot be verified (langgraph itself is absent and there is no Postgres), so this task makes the durable **declaration** and records the honest state; the install/import proof rides with Task 7's spike.

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `backend/pyproject.toml`, in `[project] dependencies`, immediately after the `"langchain-core>=1.4,<2",` line, add:

```toml
    # Durable execution-truth checkpointer (Step-1 landing; wired at Step 10).
    "langgraph-checkpoint-postgres>=2.0,<3",
```

- [ ] **Step 2: Install + import (uv-managed venv — no `pip`)**

This is a **uv** project (no `pip` in the venv). Run:
```bash
cd backend && uv add 'langgraph-checkpoint-postgres>=2.0,<3' 2>&1 | tail -5 && uv run python -c "import langgraph.checkpoint.postgres; print('POSTGRES_SAVER_OK')"
```
Expected: `POSTGRES_SAVER_OK`. (`uv add` edits `pyproject.toml`, updates `uv.lock`, and installs into `.venv` in one step — so Step 1's manual edit is redundant if you use `uv add`; either edit-then-`uv lock && uv sync` or just `uv add`. Prefer `uv add`.)

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "build(rebuild): add langgraph-checkpoint-postgres dependency (Step 1)

Declares the durable execution-truth checkpointer for the autonomous cutover
(Step 10). Install/import verification rides with the AsyncPostgresSaver spike
(Task 7), which is blocked-pending-infra in this env (no Postgres + langgraph
not installed)."
```

---

## Task 2: The per-capability semantic identity registry (pure, TDD)

`derive_identity_key` produces the stable semantic key. It is the heart of the "not a full-arg hash" requirement and is **pure** (no DB), so it is fully testable here.

**Files:**
- Create: `backend/src/services/idempotency/__init__.py`
- Create: `backend/src/services/idempotency/identity.py`
- Test: `backend/tests/idempotency/__init__.py`, `backend/tests/idempotency/test_identity_key.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/idempotency/__init__.py` (empty file).

Create `backend/tests/idempotency/test_identity_key.py`:

```python
"""The semantic identity key must be stable across recomposed args, distinct per
logical write, and never a hash of the full (volatile) payload. Pure — no DB."""

from src.services.idempotency.identity import (
    IDENTITY_SPECS,
    IdentitySpec,
    derive_identity_key,
    validate_identity_coverage,
)

_RUN = "run_1"
_STEP = "step_1"


def test_recomposed_body_yields_the_same_key():
    """The failure the ledger exists to prevent: a regenerated body must NOT
    change the identity (else resume double-fires)."""
    a = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "Hi Bob, first draft."}
    b = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "Hello Bob — REWRITTEN on resume."}
    key_a = derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0)
    key_b = derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    assert key_a == key_b


def test_recipient_change_yields_a_different_key():
    """Over-normalization guard: a genuinely different write must NOT collapse."""
    a = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "x"}
    b = {"to": "carol@acme.com", "subject": "Q3 sync", "body": "x"}
    key_a = derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0)
    key_b = derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    assert key_a != key_b


def test_recipient_order_is_normalized():
    """Recipient list order is not identity-bearing."""
    a = {"to": ["a@x.com", "b@x.com"], "subject": "s", "body": "x"}
    b = {"to": ["b@x.com", "a@x.com"], "subject": "s", "body": "x"}
    assert derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0) == (
        derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    )


def test_key_is_scoped_by_run_and_step():
    """The same logical write in two different runs is two different writes."""
    args = {"to": "bob@acme.com", "subject": "s", "body": "x"}
    k1 = derive_identity_key("email.send", args, run_id="run_A", step_id=_STEP, ordinal=0)
    k2 = derive_identity_key("email.send", args, run_id="run_B", step_id=_STEP, ordinal=0)
    assert k1 != k2
    assert "run_a" in k1.lower() and "run_b" in k2.lower()


def test_native_token_is_used_when_present():
    spec = IdentitySpec(native_token_field="idempotency_key")
    IDENTITY_SPECS["_test.native"] = spec
    try:
        args = {"idempotency_key": "tok-123", "body": "anything"}
        key = derive_identity_key("_test.native", args, run_id=_RUN, step_id=_STEP, ordinal=0)
        assert "tok-123" in key
    finally:
        del IDENTITY_SPECS["_test.native"]


def test_unregistered_write_falls_back_to_positional():
    """No spec -> args-independent positional key (fully robust to recompose)."""
    a = {"anything": "v1"}
    b = {"totally": "different"}
    k_a = derive_identity_key("unknown.write", a, run_id=_RUN, step_id=_STEP, ordinal=3)
    k_b = derive_identity_key("unknown.write", b, run_id=_RUN, step_id=_STEP, ordinal=3)
    assert k_a == k_b  # positional ignores args
    assert k_a.endswith(":pos:3")


def test_email_send_has_an_explicit_semantic_spec():
    """Known irreversible writes must carry a real (non-positional) identity."""
    spec = IDENTITY_SPECS.get("email.send")
    assert spec is not None and spec.identity_fields, "email.send needs semantic identity_fields"


def test_validate_identity_coverage_flags_specless_writes():
    missing = validate_identity_coverage({"email.send", "brand.new.write"})
    assert "brand.new.write" in missing
    assert "email.send" not in missing
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/idempotency/test_identity_key.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'src.services.idempotency'`.

- [ ] **Step 3: Write `identity.py`**

Create `backend/src/services/idempotency/__init__.py`:

```python
"""Per-step / per-tool idempotency: semantic identity keys + the ledger."""
```

Create `backend/src/services/idempotency/identity.py`:

```python
"""Per-capability SEMANTIC identity keys for the idempotency ledger.

The identity key must be:
  * STABLE across resume even when the LLM recomposes the raw args (so a
    regenerated email body does NOT change the key -> no double-fire), and
  * DISTINCT per logical write (so two genuinely different sends do NOT
    collapse into one).

It is therefore derived from the identity-DEFINING fields (recipients, subject),
NOT the full payload, and scoped to the execution position (run:step:capability).
Where a provider exposes a native idempotency token, that token IS the identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class IdentitySpec:
    """How to derive a capability's semantic identity.

    Exactly one strategy applies, checked in order:
      1. ``native_token_field`` present in args -> its value is the identity.
      2. ``identity_fields`` -> a normalized digest over just those args.
      3. otherwise -> positional (run:step:ordinal), args-independent.
    """

    native_token_field: str | None = None
    identity_fields: tuple[str, ...] = ()


# Seeded for the write capabilities that exist today (CAPABILITY_CATALOG). The
# VOLATILE fields (body/description) are DELIBERATELY excluded.
IDENTITY_SPECS: dict[str, IdentitySpec] = {
    "email.send": IdentitySpec(identity_fields=("to", "cc", "bcc", "subject")),
    "email.delete": IdentitySpec(identity_fields=("message_id",)),
    "calendar.create": IdentitySpec(
        identity_fields=("calendar_id", "start", "end", "summary", "attendees")
    ),
}


def _normalize(value: object) -> object:
    """Order-independent, whitespace/case-insensitive normalization of an
    identity field so trivial reformatting does not change the key."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, (list, tuple)):
        return sorted(_normalize(v) for v in value)  # type: ignore[type-var]
    return value


def derive_identity_key(
    capability: str,
    args: dict,
    *,
    run_id: str,
    step_id: str,
    ordinal: int,
) -> str:
    """Derive the stable semantic identity key for a write, captured at first
    attempt and reproduced verbatim on resume."""
    scope = f"{run_id}:{step_id}:{capability}"
    spec = IDENTITY_SPECS.get(capability)

    if spec and spec.native_token_field:
        token = args.get(spec.native_token_field)
        if token:
            return f"{scope}:tok:{token}"

    if spec and spec.identity_fields:
        payload = {f: _normalize(args.get(f)) for f in spec.identity_fields}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        return f"{scope}:sem:{digest}"

    # Positional fallback: args-independent -> fully robust to recompose, at the
    # cost of assuming the step re-issues its writes in the same order on resume.
    return f"{scope}:pos:{ordinal}"


def validate_identity_coverage(write_capabilities: set[str]) -> list[str]:
    """Return write capabilities that lack a registered IdentitySpec (they will
    fall back to the positional key). Surfaced as a startup WARNING; Step 3 may
    promote this to a hard startup error alongside post-condition coverage."""
    return sorted(c for c in write_capabilities if c not in IDENTITY_SPECS)
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `pytest tests/idempotency/test_identity_key.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/services/idempotency/ tests/idempotency/ && ruff format src/services/idempotency/ tests/idempotency/
git add backend/src/services/idempotency/__init__.py backend/src/services/idempotency/identity.py backend/tests/idempotency/__init__.py backend/tests/idempotency/test_identity_key.py
git commit -m "feat(rebuild): per-capability semantic identity key registry (Step 1)

derive_identity_key excludes volatile fields (body) and scopes to run:step so an
LLM-recomposed payload on resume yields the SAME key (no double-fire) while
distinct logical writes stay distinct. Native-token and positional fallbacks."
```

---

## Task 3: The `idempotency_ledger` table (model + migration + schema tests)

A new workspace-scoped table with a **`(workspace_id, identity_key)` UNIQUE index** — the DB constraint is the authoritative exactly-once gate (a second reserve of the same identity raises `IntegrityError`, exactly like the `NormalizedEvent` race fallback). Unlike the run/plan partial indexes, this one is **unconditional** (once an identity fires, it must never fire again for that run/step).

**Files:**
- Create: `backend/src/models/idempotency_ledger.py`
- Modify: `backend/src/models/__init__.py`, `backend/src/models/ids.py`
- Create: `backend/alembic/versions/a2f5c9d18b47_step_idempotency_ledger.py`
- Test: `backend/tests/idempotency/test_ledger_schema.py`

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/idempotency/test_ledger_schema.py`:

```python
"""Schema invariants for the idempotency ledger — inspected off the SQLAlchemy
table metadata (no live DB needed)."""

from src.models.idempotency_ledger import IdempotencyLedgerEntry


def test_table_name():
    assert IdempotencyLedgerEntry.__tablename__ == "idempotency_ledger"


def test_workspace_scoped_unique_index_on_identity_key():
    idx = {i.name: i for i in IdempotencyLedgerEntry.__table__.indexes}
    uq = idx.get("ix_idempotency_ledger_ws_key")
    assert uq is not None, "missing (workspace_id, identity_key) index"
    assert uq.unique is True, "identity_key index must be UNIQUE (exactly-once gate)"
    cols = [c.name for c in uq.columns]
    assert cols == ["workspace_id", "identity_key"], f"wrong columns: {cols}"


def test_workspace_id_is_not_nullable():
    col = IdempotencyLedgerEntry.__table__.c.workspace_id
    assert col.nullable is False


def test_has_status_and_result_columns():
    cols = set(IdempotencyLedgerEntry.__table__.c.keys())
    assert {"status", "result_json", "capability", "identity_key", "provider_token"} <= cols
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/idempotency/test_ledger_schema.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'src.models.idempotency_ledger'`.

- [ ] **Step 3: Create the model**

Create `backend/src/models/idempotency_ledger.py`:

```python
"""Per-step / per-tool idempotency ledger — the exactly-once gate for writes.

One row per (workspace, logical write). The (workspace_id, identity_key) UNIQUE
index is the authoritative gate: a second reserve of the same identity raises
IntegrityError, which the ledger service turns into a de-dup. Workspace-scoped
(never global) so one tenant's key can never block another's — the same
convention as TaskRun/Plan/NormalizedEvent idempotency.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class IdempotencyLedgerEntry(Base, TimestampMixin):
    __tablename__ = "idempotency_ledger"

    ledger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_flight", nullable=False)
    # in_flight, completed, failed
    provider_token: Mapped[str | None] = mapped_column(String(256))
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # UNCONDITIONAL unique: once a logical write's identity is recorded it
        # must never fire again for that run/step. Workspace-scoped, never global.
        Index("ix_idempotency_ledger_ws_key", "workspace_id", "identity_key", unique=True),
        Index("ix_idempotency_ledger_run", "run_id"),
    )
```

- [ ] **Step 4: Register the model + the `idem` id prefix**

In `backend/src/models/__init__.py`, add the import (keep the file's alphabetical-ish grouping — place it after the `from src.models.events import NormalizedEvent` line):

```python
from src.models.idempotency_ledger import IdempotencyLedgerEntry
```

And add `"IdempotencyLedgerEntry",` to `__all__` (next to `"NormalizedEvent",`).

In `backend/src/models/ids.py`, `ID_PREFIXES` is a `dict[str, str]` (prefix → entity type). Add the entry next to `"step": "task_step",`:

```python
    "idem": "idempotency_ledger",
```

- [ ] **Step 5: Run the schema test to verify it PASSES**

Run: `pytest tests/idempotency/test_ledger_schema.py -q`
Expected: all PASS.

- [ ] **Step 6: Write the migration (hand-authored — no autogenerate; no DB here)**

Create `backend/alembic/versions/a2f5c9d18b47_step_idempotency_ledger.py`:

```python
"""step idempotency ledger

Revision ID: a2f5c9d18b47
Revises: b7c1e9f3a2d4
Create Date: 2026-06-28 00:00:00.000000

Per-step / per-tool idempotency ledger (Step 1). The (workspace_id,
identity_key) UNIQUE index is the exactly-once gate.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a2f5c9d18b47"
down_revision: Union[str, None] = "b7c1e9f3a2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_ledger",
        sa.Column("ledger_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("identity_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="in_flight"),
        sa.Column("provider_token", sa.String(length=256), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id"),
    )
    op.create_index(
        "ix_idempotency_ledger_ws_key",
        "idempotency_ledger",
        ["workspace_id", "identity_key"],
        unique=True,
    )
    op.create_index("ix_idempotency_ledger_run", "idempotency_ledger", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_ledger_run", table_name="idempotency_ledger")
    op.drop_index("ix_idempotency_ledger_ws_key", table_name="idempotency_ledger")
    op.drop_table("idempotency_ledger")
```

- [ ] **Step 7: Verify the migration chain offline (no DB needed)**

Run:
```bash
cd backend && source .venv/bin/activate
python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('HEADS:', s.get_heads())"
```
Expected: `HEADS: ('a2f5c9d18b47',)` — a **single** head (the new revision), confirming the chain is linear and `down_revision` points at the prior head `b7c1e9f3a2d4`. (`alembic heads` proper also works but this bypasses any DB-config import path.)

- [ ] **Step 8: Verify metadata autogenerate would be a no-op vs the migration (parity check)**

Run:
```bash
python -c "from src.models import Base; print('idempotency_ledger' in Base.metadata.tables)"
```
Expected: `True` (the model is registered in `Base.metadata`, so the migration and the ORM agree on the table).

- [ ] **Step 9: Commit**

```bash
git add backend/src/models/idempotency_ledger.py backend/src/models/__init__.py backend/src/models/ids.py backend/alembic/versions/a2f5c9d18b47_step_idempotency_ledger.py backend/tests/idempotency/test_ledger_schema.py
git commit -m "feat(rebuild): idempotency_ledger table + migration (Step 1)

Workspace-scoped per-step ledger with an UNCONDITIONAL (workspace_id,
identity_key) UNIQUE index as the exactly-once gate. Mirrors the existing
NormalizedEvent/Plan/TaskRun workspace-scoped idempotency convention. Single
alembic head a2f5c9d18b47 -> b7c1e9f3a2d4."
```

---

## Task 4: The `IdempotencyLedger` service (reserve / record / mark_failed)

Reserve inserts `in_flight` and commits **before** the external call (so a crash after the call still leaves a durable marker). A second reserve of the same identity hits the UNIQUE index → `IntegrityError` → the service reads the existing row and reports `already_done` / `in_flight_conflict` / a retryable `failed`. This is the exact race idiom `event_processor` already uses.

**Files:**
- Modify: `backend/src/services/idempotency/ledger.py` (create)
- Test: `backend/tests/idempotency/test_ledger_sql.py`, `backend/tests/idempotency/test_ledger_service.py`

- [ ] **Step 1: Write the failing compiled-SQL test (the lookup is workspace-scoped)**

Create `backend/tests/idempotency/test_ledger_sql.py`:

```python
"""The ledger lookup must be workspace-scoped (no cross-tenant identity match).
Compiled-SQL assertion against the production statement builder (no DB)."""

from sqlalchemy.dialects import postgresql

from src.services.idempotency.ledger import _ledger_lookup_stmt


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_lookup_is_workspace_and_identity_scoped():
    sql = _compile(_ledger_lookup_stmt("ws_A", "run_1:step_1:email.send:sem:abc"))
    assert "idempotency_ledger.workspace_id = 'ws_a'" in sql
    assert "idempotency_ledger.identity_key = 'run_1:step_1:email.send:sem:abc'" in sql
```

- [ ] **Step 2: Write the failing service test (the exactly-once semantics)**

Create `backend/tests/idempotency/test_ledger_service.py`:

```python
"""IdempotencyLedger reserve/record semantics, on a mocked async session
(no live DB — matches the codebase's mocked-session test convention)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from src.services.idempotency.ledger import IdempotencyLedger


def _factory_for(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _session():
    s = AsyncMock()
    s.add = MagicMock()
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    s.flush = AsyncMock()
    return s


async def test_first_reserve_inserts_in_flight_and_proceeds():
    s = _session()
    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws", run_id="r", step_id="st", capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.already_done is False and out.in_flight_conflict is False
    s.add.assert_called_once()
    s.commit.assert_awaited()


async def test_resume_with_completed_entry_short_circuits():
    """Second reserve of the same identity -> IntegrityError -> read existing
    completed row -> already_done with the stored result (caller skips the call)."""
    s = _session()
    s.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))
    existing = SimpleNamespace(
        status="completed", result_json={"status": "sent", "id": "msg_1"}, ledger_id="idem_x",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    s.execute = AsyncMock(return_value=result)

    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws", run_id="r", step_id="st", capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.already_done is True
    assert out.result == {"status": "sent", "id": "msg_1"}
    s.rollback.assert_awaited()


async def test_resume_with_in_flight_entry_reports_conflict():
    s = _session()
    s.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))
    existing = SimpleNamespace(status="in_flight", result_json=None, ledger_id="idem_y")
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    s.execute = AsyncMock(return_value=result)

    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws", run_id="r", step_id="st", capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.in_flight_conflict is True
    assert out.already_done is False


async def test_record_success_updates_row():
    s = _session()
    row = SimpleNamespace(status="in_flight", result_json=None, completed_at=None)
    s.get = AsyncMock(return_value=row)
    ledger = IdempotencyLedger(_factory_for(s))
    await ledger.record_success("idem_x", {"status": "sent"})
    assert row.status == "completed"
    assert row.result_json == {"status": "sent"}
    s.commit.assert_awaited()
```

- [ ] **Step 3: Run both to verify they FAIL**

Run: `pytest tests/idempotency/test_ledger_sql.py tests/idempotency/test_ledger_service.py -q`
Expected: FAIL at collection — `cannot import name '_ledger_lookup_stmt' / 'IdempotencyLedger' from 'src.services.idempotency.ledger'`.

- [ ] **Step 4: Write `ledger.py`**

Create `backend/src/services/idempotency/ledger.py`:

```python
"""The idempotency ledger service: reserve-before-write, record-on-success.

The (workspace_id, identity_key) UNIQUE index is the authoritative gate; the
inline lookup + IntegrityError-on-commit fallback mirrors event_processor's
concurrent-dedup pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from src.models.idempotency_ledger import IdempotencyLedgerEntry

logger = logging.getLogger(__name__)


def _ledger_lookup_stmt(workspace_id: str, identity_key: str) -> Select:
    """Workspace-scoped lookup of an existing ledger entry. Extracted so the
    isolation test can compile it (no DB)."""
    return select(IdempotencyLedgerEntry).where(
        IdempotencyLedgerEntry.workspace_id == workspace_id,
        IdempotencyLedgerEntry.identity_key == identity_key,
    )


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    already_done: bool          # a completed entry exists -> skip the call, use result
    in_flight_conflict: bool    # an in_flight entry exists (killed mid-call) -> do NOT re-fire
    result: dict | None         # stored result when already_done
    identity_key: str
    ledger_id: str | None       # the reserved row to record_success/mark_failed against


class IdempotencyLedger:
    def __init__(self, db_factory):
        self._db_factory = db_factory

    async def reserve(
        self,
        *,
        workspace_id: str,
        run_id: str | None,
        step_id: str | None,
        capability: str,
        identity_key: str,
        provider_token: str | None = None,
    ) -> ReserveOutcome:
        ledger_id = f"idem_{ULID()}"
        async with self._db_factory() as db:
            entry = IdempotencyLedgerEntry(
                ledger_id=ledger_id,
                workspace_id=workspace_id,
                run_id=run_id,
                step_id=step_id,
                capability=capability,
                identity_key=identity_key,
                status="in_flight",
                provider_token=provider_token,
            )
            db.add(entry)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return await self._resolve_existing(db, workspace_id, identity_key)
            return ReserveOutcome(
                already_done=False, in_flight_conflict=False, result=None,
                identity_key=identity_key, ledger_id=ledger_id,
            )

    async def _resolve_existing(self, db, workspace_id: str, identity_key: str) -> ReserveOutcome:
        result = await db.execute(_ledger_lookup_stmt(workspace_id, identity_key))
        row = result.scalar_one_or_none()
        if row is None:
            # Lost/rolled-back row (rare) — proceed as a fresh reservation.
            logger.warning("[idempotency] conflict with no row for key=%s", identity_key)
            return ReserveOutcome(False, False, None, identity_key, None)
        if row.status == "completed":
            return ReserveOutcome(True, False, row.result_json, identity_key, row.ledger_id)
        if row.status == "failed":
            # The prior attempt failed (effect did not land) -> allow a retry by
            # reopening the reservation.
            row.status = "in_flight"
            row.result_json = None
            await db.commit()
            return ReserveOutcome(False, False, None, identity_key, row.ledger_id)
        # in_flight: killed mid-call -> fail-closed against a double-fire.
        return ReserveOutcome(False, True, None, identity_key, row.ledger_id)

    async def record_success(self, ledger_id: str, result: dict | None) -> None:
        async with self._db_factory() as db:
            row = await db.get(IdempotencyLedgerEntry, ledger_id)
            if row is None:
                logger.warning("[idempotency] record_success: no row %s", ledger_id)
                return
            row.status = "completed"
            row.result_json = result
            row.completed_at = datetime.now(timezone.utc)
            await db.commit()

    async def mark_failed(self, ledger_id: str) -> None:
        async with self._db_factory() as db:
            row = await db.get(IdempotencyLedgerEntry, ledger_id)
            if row is None:
                return
            row.status = "failed"
            await db.commit()
```

- [ ] **Step 5: Run both to verify they PASS**

Run: `pytest tests/idempotency/test_ledger_sql.py tests/idempotency/test_ledger_service.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint + commit**

```bash
ruff check src/services/idempotency/ledger.py tests/idempotency/ && ruff format src/services/idempotency/ledger.py
git add backend/src/services/idempotency/ledger.py backend/tests/idempotency/test_ledger_sql.py backend/tests/idempotency/test_ledger_service.py
git commit -m "feat(rebuild): IdempotencyLedger reserve/record service (Step 1)

reserve() inserts in_flight + commits BEFORE the external call; a second reserve
of the same identity hits the UNIQUE index -> IntegrityError -> reads the
existing row (completed=skip+return result, in_flight=fail-closed conflict,
failed=reopen-for-retry). Mirrors event_processor's dedup-race idiom."
```

---

## Task 5: The DI wrapper — `make_idempotent_execute_tool_fn` (the exactly-once proof, runnable here)

This is the seam that makes the whole thing real: it wraps the injected `execute_tool_fn`, applying the ledger to **write** capabilities only. Its test is the **runnable-here proxy for the acceptance test** — a re-fire with *different recomposed args* does **not** double-call the inner tool.

**Files:**
- Create: `backend/src/services/idempotency/wrapper.py`
- Test: `backend/tests/idempotency/test_idempotent_wrapper.py`

- [ ] **Step 1: Write the failing wrapper test**

Create `backend/tests/idempotency/test_idempotent_wrapper.py`:

```python
"""The wrapper is the injected-execute_tool_fn seam. The key property: on a
'resume' (a second call for the same run/step/capability) with RECOMPOSED args,
the inner tool is called EXACTLY ONCE. Mocked ledger + mocked inner (no DB)."""

from unittest.mock import AsyncMock, MagicMock

from src.services.idempotency.ledger import ReserveOutcome
from src.services.idempotency.wrapper import IdempotencyContext, make_idempotent_execute_tool_fn


def _ctx(ledger):
    return IdempotencyContext(
        ledger=ledger, run_id="r", step_id="st", workspace_id="ws", db_factory=MagicMock()
    )


async def _write_cap_resolver(monkeypatched_calls):
    """Patched into the wrapper: email.send is a write; anything else is read."""
    async def _resolve(tool_name, db_factory, workspace_id):
        monkeypatched_calls.append(tool_name)
        return ("email.send", True) if tool_name == "send_gmail_message" else ("email.read", False)
    return _resolve


async def test_read_capability_bypasses_the_ledger(monkeypatch):
    inner = AsyncMock(return_value={"ok": True})
    ledger = AsyncMock()
    calls: list[str] = []
    monkeypatch.setattr(
        "src.services.idempotency.wrapper._resolve_capability_is_write",
        await _write_cap_resolver(calls),
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger))
    await fn("get_gmail_message_content", {"id": "1"}, user_id="u", workspace_id="ws")
    inner.assert_awaited_once()
    ledger.reserve.assert_not_called()


async def test_first_write_reserves_and_records(monkeypatch):
    inner = AsyncMock(return_value={"status": "sent", "id": "msg_1"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(False, False, None, "r:st:email.send:sem:1", "idem_1")
    )
    monkeypatch.setattr(
        "src.services.idempotency.wrapper._resolve_capability_is_write",
        await _write_cap_resolver([]),
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger))
    out = await fn("send_gmail_message", {"to": "b@x.com", "subject": "s", "body": "draft-1"},
                   user_id="u", workspace_id="ws")
    inner.assert_awaited_once()
    ledger.record_success.assert_awaited_once_with("idem_1", {"status": "sent", "id": "msg_1"})
    assert out == {"status": "sent", "id": "msg_1"}


async def test_resume_with_recomposed_args_does_not_double_fire(monkeypatch):
    """THE acceptance property (unit proxy): the ledger says already_done, so the
    inner tool is NOT called again even though the body changed on resume."""
    inner = AsyncMock(return_value={"status": "sent", "id": "msg_1"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(True, False, {"status": "sent", "id": "msg_1"},
                                    "r:st:email.send:sem:1", "idem_1")
    )
    monkeypatch.setattr(
        "src.services.idempotency.wrapper._resolve_capability_is_write",
        await _write_cap_resolver([]),
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger))
    out = await fn("send_gmail_message",
                   {"to": "b@x.com", "subject": "s", "body": "RECOMPOSED on resume"},
                   user_id="u", workspace_id="ws")
    inner.assert_not_awaited()  # exactly-once: the second fire is suppressed
    ledger.record_success.assert_not_called()
    assert out == {"status": "sent", "id": "msg_1"}  # returns the first-attempt result


async def test_in_flight_conflict_is_not_refired(monkeypatch):
    inner = AsyncMock(return_value={"status": "sent"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(False, True, None, "r:st:email.send:sem:1", "idem_1")
    )
    monkeypatch.setattr(
        "src.services.idempotency.wrapper._resolve_capability_is_write",
        await _write_cap_resolver([]),
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger))
    out = await fn("send_gmail_message", {"to": "b@x.com", "subject": "s", "body": "x"},
                   user_id="u", workspace_id="ws")
    inner.assert_not_awaited()
    assert out.get("idempotent_uncertain") is True


async def test_failed_inner_result_marks_failed(monkeypatch):
    inner = AsyncMock(return_value={"error": "smtp down", "is_error": True})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(False, False, None, "r:st:email.send:sem:1", "idem_1")
    )
    monkeypatch.setattr(
        "src.services.idempotency.wrapper._resolve_capability_is_write",
        await _write_cap_resolver([]),
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger))
    await fn("send_gmail_message", {"to": "b@x.com", "subject": "s", "body": "x"},
             user_id="u", workspace_id="ws")
    ledger.mark_failed.assert_awaited_once_with("idem_1")
    ledger.record_success.assert_not_called()
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/idempotency/test_idempotent_wrapper.py -q`
Expected: FAIL at collection — `cannot import name 'IdempotencyContext' / 'make_idempotent_execute_tool_fn'`.

- [ ] **Step 3: Write `wrapper.py`**

Create `backend/src/services/idempotency/wrapper.py`:

```python
"""The idempotency DI seam.

`make_idempotent_execute_tool_fn` wraps the injected `execute_tool_fn`. Only the
AUTONOMOUS path (step_runner) installs it; the chat path injects the plain
execute_tool_fn, so chat is untouched (idempotency is an autonomous-path property).

Write capabilities go through the ledger; read capabilities bypass it entirely.
The identity key is derived per capability (semantic fields / native token /
positional), captured at first attempt and reproduced on resume — so an
LLM-recomposed payload cannot double-fire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import count

from src.services.idempotency.identity import derive_identity_key
from src.services.idempotency.ledger import IdempotencyLedger

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IdempotencyContext:
    ledger: IdempotencyLedger
    run_id: str
    step_id: str
    workspace_id: str
    db_factory: object


async def _resolve_capability_is_write(
    tool_name: str, db_factory, workspace_id: str
) -> tuple[str | None, bool]:
    """Resolve (capability, is_write) for a tool via the registry. Isolated as a
    module function so tests can monkeypatch it."""
    from src.services.capability_resolver import CapabilityResolver
    from src.services.tool_registry import ToolRegistry

    async with db_factory() as db:
        registry = ToolRegistry(db, workspace_id=workspace_id or None)
        tool = await registry.get_tool(tool_name)
        capability = getattr(tool, "capability", None) if tool else None
        if not capability:
            return None, False
        is_write = await CapabilityResolver(db, workspace_id=workspace_id or "").is_write_capability(
            capability
        )
        return capability, is_write


def make_idempotent_execute_tool_fn(inner_execute_tool_fn, ctx: IdempotencyContext):
    """Return an execute_tool_fn that applies the idempotency ledger to writes."""
    ordinal_counter = count()

    async def _idempotent_execute(tool_name, tool_input, *, user_id, workspace_id):
        capability, is_write = await _resolve_capability_is_write(
            tool_name, ctx.db_factory, workspace_id or ctx.workspace_id
        )
        if not is_write or capability is None:
            return await inner_execute_tool_fn(
                tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
            )

        ordinal = next(ordinal_counter)
        identity_key = derive_identity_key(
            capability, tool_input or {}, run_id=ctx.run_id, step_id=ctx.step_id, ordinal=ordinal
        )
        outcome = await ctx.ledger.reserve(
            workspace_id=workspace_id or ctx.workspace_id,
            run_id=ctx.run_id, step_id=ctx.step_id,
            capability=capability, identity_key=identity_key,
        )
        if outcome.already_done:
            logger.info("[idempotency] %s SKIP (already completed) key=%s", tool_name, identity_key)
            return outcome.result
        if outcome.in_flight_conflict:
            logger.warning(
                "[idempotency] %s NOT re-fired — prior attempt in-flight key=%s", tool_name, identity_key
            )
            return {
                "error": "idempotency: prior attempt in-flight; not re-fired (awaiting verification)",
                "idempotent_uncertain": True,
            }

        result = await inner_execute_tool_fn(
            tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
        )
        is_err = isinstance(result, dict) and (result.get("error") or result.get("is_error"))
        if is_err:
            await ctx.ledger.mark_failed(outcome.ledger_id)
        else:
            await ctx.ledger.record_success(outcome.ledger_id, result)
        return result

    return _idempotent_execute
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `pytest tests/idempotency/test_idempotent_wrapper.py -q`
Expected: all PASS (including `test_resume_with_recomposed_args_does_not_double_fire` — the exactly-once proxy).

- [ ] **Step 5: Update the package exports**

In `backend/src/services/idempotency/__init__.py`, add:

```python
from src.services.idempotency.identity import derive_identity_key
from src.services.idempotency.ledger import IdempotencyLedger, ReserveOutcome
from src.services.idempotency.wrapper import IdempotencyContext, make_idempotent_execute_tool_fn

__all__ = [
    "derive_identity_key",
    "IdempotencyLedger",
    "ReserveOutcome",
    "IdempotencyContext",
    "make_idempotent_execute_tool_fn",
]
```

- [ ] **Step 6: Lint + commit**

```bash
ruff check src/services/idempotency/ tests/idempotency/ && ruff format src/services/idempotency/
git add backend/src/services/idempotency/wrapper.py backend/src/services/idempotency/__init__.py backend/tests/idempotency/test_idempotent_wrapper.py
git commit -m "feat(rebuild): idempotent execute_tool_fn wrapper (Step 1)

The DI seam: wraps the injected execute_tool_fn, applying the ledger to writes
only. Proves the exactly-once property (a resume with recomposed args does NOT
double-call the inner tool). in_flight-on-resume is fail-closed (not re-fired)."
```

---

## Task 6: Wire the wrapper into the autonomous path (`step_runner`), chat path untouched

Install the wrapper at the one seam where `run`+`step` are in scope, just before `agent_loop(...)`. The chat path never reaches this code.

**Files:**
- Modify: `backend/src/services/step_runner.py` (`run_step_via_agent_loop`)
- Test: `backend/tests/idempotency/test_step_runner_wiring.py` (create)

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/idempotency/test_step_runner_wiring.py`:

```python
"""run_step_via_agent_loop must hand agent_loop an IDEMPOTENT execute_tool_fn
(wrapped), not the raw one — proving the ledger is installed on the autonomous
path. We intercept agent_loop and inspect the execute_tool_fn it receives."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.step_runner import StepRunner


def _runner():
    r = StepRunner.__new__(StepRunner)
    r._settings = SimpleNamespace(resolved_model="m")
    r._client = MagicMock()
    r._store = MagicMock()
    r._store.get_all_steps = AsyncMock(return_value=[])
    r._emitter = MagicMock()
    # _db_factory and _active_traces are read-only @property accessors that call
    # the *_provider(); set the providers, not the properties.
    r._db_factory_provider = MagicMock(return_value=MagicMock())
    r._active_traces_provider = MagicMock(return_value={})
    r._execute_tool_fn = AsyncMock(name="RAW_execute_tool_fn")
    r._budget = None
    r._circuit_breaker = None
    return r


@pytest.mark.asyncio
async def test_agent_loop_receives_a_wrapped_execute_tool_fn():
    runner = _runner()
    step = SimpleNamespace(step_id="st", input_data={"capability": "email.send"}, status="running")
    run = SimpleNamespace(run_id="r", user_id="u", workspace_id="ws")

    captured = {}

    async def fake_agent_loop(*args, **kwargs):
        captured["execute_tool_fn"] = kwargs.get("execute_tool_fn")
        if False:
            yield  # make this an async generator
        return

    with (
        patch("src.orchestrator.agent_loop.agent_loop", fake_agent_loop),
        patch("src.orchestrator.agents.AGENTS", {"operator": SimpleNamespace(prompt="p")}),
        patch.object(runner, "build_step_context", AsyncMock(return_value="")),
        patch.object(runner, "build_operator_tools", AsyncMock(return_value=[])),
    ):
        await runner.run_step_via_agent_loop(step, run)

    fn = captured["execute_tool_fn"]
    assert fn is not None
    assert fn is not runner._execute_tool_fn, "agent_loop got the RAW fn — ledger not installed"
    assert fn.__name__ == "_idempotent_execute"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/idempotency/test_step_runner_wiring.py -q`
Expected: FAIL — the assertion `fn is not runner._execute_tool_fn` fails (today the raw fn is passed).

- [ ] **Step 3: Wire the wrapper in `run_step_via_agent_loop`**

In `backend/src/services/step_runner.py`, inside `run_step_via_agent_loop`, **immediately before** the `async for event in agent_loop(` call, insert:

```python
        # Install the per-step idempotency ledger on the injected execute_tool_fn
        # (autonomous path only — the chat path passes the raw fn, so it stays a
        # no-op there). Writes go through the ledger keyed on a semantic identity
        # so an LLM-recomposed payload on resume cannot double-fire (Step 1).
        from src.services.idempotency import (
            IdempotencyContext,
            IdempotencyLedger,
            make_idempotent_execute_tool_fn,
        )

        idem_execute_tool_fn = self._execute_tool_fn
        if self._execute_tool_fn is not None:
            idem_execute_tool_fn = make_idempotent_execute_tool_fn(
                self._execute_tool_fn,
                IdempotencyContext(
                    ledger=IdempotencyLedger(self._db_factory),
                    run_id=run.run_id,
                    step_id=step.step_id,
                    workspace_id=run.workspace_id or "",
                    db_factory=self._db_factory,
                ),
            )
```

Then change the `agent_loop(...)` call's argument from:

```python
            execute_tool_fn=self._execute_tool_fn,
```
to:
```python
            execute_tool_fn=idem_execute_tool_fn,
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `pytest tests/idempotency/test_step_runner_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm the chat path is untouched (grep guard)**

Run:
```bash
grep -rn "make_idempotent_execute_tool_fn\|IdempotencyContext" src/orchestrator/ || echo "CHAT_PATH_CLEAN"
```
Expected: `CHAT_PATH_CLEAN` — the wrapper is installed only in `src/services/step_runner.py` (the autonomous seam), never in the chat orchestrator.

- [ ] **Step 6: Full-suite regression (no deep_runtime) + lint**

Run:
```bash
ruff check src/services/step_runner.py && pytest tests/ -q --ignore=tests/deep_runtime -p no:cacheprovider 2>&1 | tail -5
```
Expected: ruff clean; full suite PASS (existing step_runner tests still green — the wrap is transparent when the ledger reports proceed).

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/step_runner.py backend/tests/idempotency/test_step_runner_wiring.py
git commit -m "feat(rebuild): install idempotency ledger on the autonomous step path (Step 1)

run_step_via_agent_loop wraps the injected execute_tool_fn with the idempotency
ledger before agent_loop. Chat path passes the raw fn -> untouched by
construction (idempotency is an autonomous-path property, like the trust gate)."
```

---

## Task 7: SPIKE — `AsyncPostgresSaver` durable resume + non-pickle serializer (blocked-pending-infra)

Prove the spec's premise — LangGraph durability is at-least-once (the node replays), and the Step-1 ledger is what makes that replay safe (exactly-once). **This env cannot run it** (no Postgres + langgraph not installed), so ship a **ready-to-run** probe + an **honest** finding; do **not** fake results. The runnable-here exactly-once proof is Task 5's `test_resume_with_recomposed_args_does_not_double_fire`.

**Files:**
- Create: `backend/spikes/postgres_saver/__init__.py`, `backend/spikes/postgres_saver/probe.py`
- Modify: `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md`

- [ ] **Step 1: Write the ready-to-run probe**

Create `backend/spikes/postgres_saver/__init__.py` (empty).

Create `backend/spikes/postgres_saver/probe.py`:

```python
"""SPIKE (Step 1): AsyncPostgresSaver durable resume + exactly-once + non-pickle serializer.

Run ONLY where dev Postgres is up AND langgraph-checkpoint-postgres is installed:
    docker compose up -d
    cd backend && source .venv/bin/activate && pip install -e .
    JARVIS_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis \
        python -m spikes.postgres_saver.probe

Proves:
  1. Durable resume: kill after the side effect, resume the same thread_id -> the
     node re-runs from the top (at-least-once replay).
  2. Exactly-once: a per-(thread_id) idempotency guard (the Step-1 ledger analog)
     makes the EFFECT fire exactly once across kill+resume.
  3. A non-pickle serializer round-trips checkpoint state.
"""

from __future__ import annotations

import asyncio
import os


async def main() -> int:
    db_url = os.environ.get("JARVIS_DATABASE_URL")
    if not db_url:
        print("SKIP: set JARVIS_DATABASE_URL to a running Postgres.")
        return 2
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command
    except ModuleNotFoundError as e:
        print(f"BLOCKED: {e}. Install langgraph-checkpoint-postgres (Task 1).")
        return 2

    from typing import Annotated, TypedDict

    effect_fires: list[str] = []          # the recorded external side effect
    seen_identity: set[str] = set()       # the idempotency guard (ledger analog)

    class S(TypedDict):
        step: Annotated[int, lambda a, b: b]

    def side_effect_node(state: S) -> S:
        identity = "run-1:step-1:email.send:sem:FIXED"  # stable across resume
        if identity not in seen_identity:
            seen_identity.add(identity)
            effect_fires.append(identity)                # <-- the "API call"
            raise RuntimeError("KILL: after effect, before checkpoint")  # first pass only
        return {"step": 1}

    g = StateGraph(S)
    g.add_node("send", side_effect_node)
    g.add_edge(START, "send")
    g.add_edge("send", END)

    serde = JsonPlusSerializer()  # non-pickle
    async with AsyncPostgresSaver.from_conn_string(db_url, serde=serde) as saver:
        await saver.setup()
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "spike-thread-1"}}

        # First pass: side effect fires, then we crash before checkpoint.
        try:
            await graph.ainvoke({"step": 0}, cfg, durability="sync")
        except RuntimeError as e:
            print(f"pass-1 crashed as expected: {e}")

        # Resume the same thread_id: node replays; the guard suppresses re-fire.
        await graph.ainvoke(Command(resume=None), cfg, durability="sync")

    fired = len(effect_fires)
    print(f"effect fired {fired} time(s); exactly-once={fired == 1}")
    return 0 if fired == 1 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Attempt to run it (expected: blocked here)**

Run:
```bash
cd backend && source .venv/bin/activate && python -m spikes.postgres_saver.probe 2>&1 | tail -5
```
Record the exact output. In this env, expect `BLOCKED: No module named 'langgraph'...` (or a Postgres connection error if langgraph were present). **Do not** fake a green result. If, and only if, you are in an env with Postgres + the install, capture the real `exactly-once=True` line.

- [ ] **Step 3: Update the finding doc honestly**

Replace `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md` with the updated status: dependency landed (Task 1), **still blocked-pending-infra** in this environment for **two** reasons now — (a) no Postgres, (b) `langgraph`/`langgraph-checkpoint-postgres` not installed in this venv (only `langchain-core`+`anthropic` are). Include: the exact `python -m spikes.postgres_saver.probe` output observed; the probe path; the ready-to-run command block; and the note that the **runnable-here** exactly-once proof already exists as `tests/idempotency/test_idempotent_wrapper.py::test_resume_with_recomposed_args_does_not_double_fire`. State plainly this gates **Step 10** (autonomous cutover), not Step 1's ledger landing.

- [ ] **Step 4: Commit**

```bash
git add backend/spikes/postgres_saver/__init__.py backend/spikes/postgres_saver/probe.py docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md
git commit -m "spike(rebuild): ready-to-run AsyncPostgresSaver probe; still blocked-pending-infra (Step 1)

Probe proves durable-resume + exactly-once (via the ledger analog) + non-pickle
serializer when Postgres + langgraph-checkpoint-postgres are available. Blocked
in this env (no Postgres + langgraph not installed) — NOT faked. Runnable-here
exactly-once proof: test_idempotent_wrapper::...does_not_double_fire."
```

---

## Final verification

- [ ] **Run the Step-1 tests + the full non-deep_runtime suite + lint**

Run:
```bash
cd backend && source .venv/bin/activate
ruff check src/services/idempotency/ src/models/idempotency_ledger.py src/services/step_runner.py tests/idempotency/
pytest tests/idempotency/ -q
pytest tests/ -q --ignore=tests/deep_runtime -p no:cacheprovider 2>&1 | tail -5
```
Expected: ruff clean; `tests/idempotency/` all PASS; full non-deep_runtime suite PASS (no regression from the step_runner wrap).

- [ ] **Confirm Step-1 exit criteria**
  - A per-capability **semantic identity key** exists (`derive_identity_key`) that yields the **same** key for recomposed args and **distinct** keys for distinct writes (Task 2 tests prove both).
  - The `idempotency_ledger` table + migration exist with a workspace-scoped **UNIQUE** `(workspace_id, identity_key)` index; single alembic head `a2f5c9d18b47 → b7c1e9f3a2d4` (Task 3).
  - `IdempotencyLedger.reserve/record_success/mark_failed` enforce exactly-once via the UNIQUE index + IntegrityError fallback (Task 4).
  - The autonomous step path installs the wrapper; the **chat path is untouched** (grep guard, Task 6). The exactly-once-on-recompose property is proven by a runnable test (Task 5).
  - `langgraph-checkpoint-postgres` is declared in `pyproject.toml` (Task 1).
  - The `AsyncPostgresSaver` kill/resume spike is **ready-to-run** and honestly **blocked-pending-infra** — not faked (Task 7).
  - **Deferred to later steps (unchanged):** in_flight-on-resume read-back/compensation is **Step 3** (verification); the cross-path write lock is **Step 6**; the actual `AsyncPostgresSaver` wiring is **Step 10**; `build_deep_agent` gains a `checkpointer` param at **Step 6**; `agents.workspace_id` is its own "Step A" plan.

---

## Self-review (against spec §6 Step 1 + §4.8 + §7)

- **"per-capability semantic identity key (or provider-native token) captured at first-attempt time — not a full-arg hash"** → Task 2 (`identity.py`): semantic fields exclude the volatile body; native-token + positional fallbacks; captured at first attempt, reproduced on resume. ✅
- **"LLM-recomposed payloads on resume can't double-fire"** → Task 2 `test_recomposed_body_yields_the_same_key` + Task 5 `test_resume_with_recomposed_args_does_not_double_fire`. ✅
- **"over-normalization that collapses two distinct legitimate writes is the opposite failure"** → Task 2 `test_recipient_change_yields_a_different_key`. ✅
- **"per-step keys do not exist today (only TaskRun/Plan/NormalizedEvent)"** → net-new `idempotency_ledger` table, mirroring their workspace-scoped convention. ✅
- **"langgraph-checkpoint-postgres lands here"** → Task 1. ✅
- **"Acceptance: kill after the write's API call but before checkpoint, resume where recomposed raw args differ, assert fired exactly once"** → Task 7 probe (blocked-pending-infra, ready-to-run) + Task 5 runnable-here unit proxy. ✅
- **"HARD prerequisite before autonomous cutover"** → the ledger gates Step 10; documented in exit criteria + the spike doc. ✅
- **Schema-touching step needs an in-flight-run posture** (spec §6 preamble): the migration is **additive** (new table, no change to existing rows), so no drain/dual-read is required; existing runs simply gain ledger coverage on their next step. Noted here rather than adding a task. ✅
