"""drop governor agent row

Data-only cleanup (no schema change). The Governor LLM *agent* identity was removed
in code (Step 7A P2, 7→6 agents): its ``AGENT_PROMPTS`` entry, ``GOVERNOR_PROMPT``,
model tier, thinking config, and capability scope are all gone, and nothing routes to
it (the capability router never returns ``"governor"``; ``edge_case_only`` is read
nowhere). ``AgentRegistry.seed_defaults()`` CREATES/UPDATES agents on startup but NEVER
DELETES — so on the next restart it maintains the remaining six agents while leaving the
stranded, unrouted ``governor`` row behind. This migration removes that dangling row.

The Governor *service* (``src/services/governor.py``), the ``governor_pre_tool_hook``
audit hook, and the ``report_governor_verdict``/``evaluate_policy`` tools + their
``internal.*`` capabilities are deliberately KEPT (orphaned-but-harmless); this migration
only drops the dead *agent* row, not the service/hook/tool layer.

FK-safety finding: a repo-wide sweep of ``src/`` and ``alembic/`` found NO ``ForeignKey``
referencing the ``agents`` table PK (``agents.agent_id``) or ``name``. Agents are
referenced by NAME string only (e.g. ``classify_capability_agent``; ``load_as_sub_agents``
keys ``SubAgent`` by ``agent.name``). Deleting the row therefore has no ON DELETE cascade
surprise — no ``task_runs``/``task_steps`` or other table holds an FK to it.

Seed rows use ``agent_id=f"agt_{ULID()}"`` and ``name="governor"``, so the stray row's
``agent_id`` is ``agt_<ULID>`` (NOT literally "governor"). The DELETE targets
``name = 'governor'``; the ``agent_id = 'governor'`` clause is harmless
belt-and-suspenders for any hand-inserted row.

Revision ID: 1a2770a28c39
Revises: 574f6c145bca
Create Date: 2026-07-08 09:46:20.767423
"""
from typing import Sequence, Union

from alembic import op

revision: str = '1a2770a28c39'
down_revision: Union[str, None] = '574f6c145bca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: removes the dangling governor row if present, no-op otherwise.
    op.execute("DELETE FROM agents WHERE agent_id = 'governor' OR name = 'governor'")


def downgrade() -> None:
    # Intentional NO-OP. The ``governor`` agent no longer exists in code, so re-inserting
    # a dead, unrouted row on downgrade serves no purpose — nothing routes to it and
    # ``seed_defaults`` would not maintain it. Keeping downgrade empty preserves a valid
    # alembic down/up round-trip: downgrade is a clean no-op and re-upgrade re-runs the
    # idempotent DELETE above.
    pass
