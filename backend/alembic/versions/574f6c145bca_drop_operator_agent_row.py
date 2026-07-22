"""drop operator agent row

Data-only cleanup (no schema change). The ``operator`` agent identity was renamed
to ``executor`` in code (``AGENT_PROMPTS``, ``classify_capability_agent``, etc.).
``AgentRegistry.seed_defaults()`` CREATES/UPDATES agents on startup but NEVER
DELETES — so on the next restart it creates the new ``executor`` row while leaving
the stranded, unrouted ``operator`` row behind (nothing routes to it anymore). This
migration removes that dangling row.

FK-safety finding (Step 1): a repo-wide sweep of ``src/`` and ``alembic/`` found NO
``ForeignKey`` referencing the ``agents`` table PK (``agents.agent_id``) or ``name``.
Agents are referenced by NAME string only (e.g. ``classify_capability_agent`` returns
``"executor"``; ``load_as_sub_agents`` keys ``SubAgent`` by ``agent.name``). Deleting
the row therefore has no ON DELETE cascade surprise — no ``task_runs``/``task_steps``
or other table holds an FK to it.

Seed rows use ``agent_id=f"agt_{ULID()}"`` and ``name="operator"``, so the stray row's
``agent_id`` is ``agt_<ULID>`` (NOT literally "operator"). The DELETE targets
``name = 'operator'``; the ``agent_id = 'operator'`` clause is harmless
belt-and-suspenders for any hand-inserted row.

Revision ID: 574f6c145bca
Revises: 8af9ae555c87
Create Date: 2026-07-08 01:54:53.249739
"""
from typing import Sequence, Union

from alembic import op

revision: str = '574f6c145bca'
down_revision: Union[str, None] = '8af9ae555c87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: removes the dangling operator row if present, no-op otherwise.
    op.execute("DELETE FROM agents WHERE agent_id = 'operator' OR name = 'operator'")


def downgrade() -> None:
    # Intentional NO-OP. The ``operator`` agent no longer exists in code, so re-inserting
    # a dead, unrouted row on downgrade serves no purpose — nothing routes to it and
    # ``seed_defaults`` would not maintain it. Keeping downgrade empty preserves a valid
    # alembic down/up round-trip: downgrade is a clean no-op and re-upgrade re-runs the
    # idempotent DELETE above.
    pass
