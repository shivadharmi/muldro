"""widen observation_cursor cursor_value to Text

Revision ID: c3d5e7f9a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-20 00:00:00.000000

Task 3.4 (Slack per-channel cursor): the Slack connector now stores a per-channel
JSON map ``{channel_id: last_ts}`` in ``cursor_value`` instead of a single bare
``ts`` string. On a large workspace this map can exceed the previous
``VARCHAR(512)`` limit, which would silently truncate the JSON and corrupt the
cursor (losing per-channel watermarks → message loss).

Widening ``cursor_value`` from ``VARCHAR(512)`` to ``TEXT`` is fully
backward-compatible:
  - Existing short opaque cursors (gmail historyId, calendar syncToken, github
    timestamp, legacy slack bare-ts) fit unchanged.
  - No truncation can occur on existing data (Text is strictly wider).
  - The connector tolerates a legacy bare-string cursor by treating it as an
    empty per-channel map (one-time re-scan), so old rows never crash the poll.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "observation_cursors",
        "cursor_value",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Reverting to VARCHAR(512) can fail if any row now holds a per-channel map
    # longer than 512 chars. Truncate defensively before narrowing so the
    # downgrade does not error; the cursor will re-scan affected channels.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE observation_cursors SET cursor_value = left(cursor_value, 512) "
            "WHERE length(cursor_value) > 512"
        )
    )
    op.alter_column(
        "observation_cursors",
        "cursor_value",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
