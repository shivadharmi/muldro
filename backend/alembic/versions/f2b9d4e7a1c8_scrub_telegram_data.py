"""scrub telegram data

Revision ID: f2b9d4e7a1c8
Revises: c3f1a2b4d5e6
Create Date: 2026-06-16 00:00:00.000000

Destructive, irreversible data scrub for the removed Telegram integration.
The Telegram surface, its MCP tools, and its delivery path were deleted across
the code in the preceding phases; this migration removes the orphaned rows they
left behind.

`surface`/`channel` are free-text String(32) columns (never DB enums), so there
is NO schema change here — only data is affected:

  - tool_definitions: the seeded `send_telegram` / `send_approval_prompt` rows are
    orphaned because ToolRegistry.seed_defaults() is upsert-only and never deletes
    tools dropped from the catalog. Delete them.
  - conversations / messages with surface='telegram': delete (children first;
    messages.conversation_id is ON DELETE CASCADE, so this is belt-and-suspenders
    and also sweeps any orphan telegram messages).
  - notifications with channel='telegram': delete (undeliverable now).
  - sessions with surface='telegram': retag to 'web' — never delete the row.
    NOTE: the `surface` column lives on the `sessions` table, NOT `users` (the
    User model has no surface column). The original spec said "users.surface";
    that was a misread of users.py, where the column belongs to the Session model
    lower in the same file.

This permanently deletes Telegram conversation history and notifications.
Acceptable per owner decision (pre-release product, surface retired). The Redis
`surface_registry` telegram keys are cleared at deploy time (runtime step, not
SQL) and are TTL'd, so they also expire on their own.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f2b9d4e7a1c8"
down_revision: Union[str, None] = "c3f1a2b4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Children first (messages → conversations), though the FK cascade would
    # also handle messages when the parent conversation is deleted.
    op.execute("DELETE FROM messages WHERE surface = 'telegram'")
    op.execute("DELETE FROM conversations WHERE surface = 'telegram'")

    # Undeliverable notifications bound to the removed channel.
    op.execute("DELETE FROM notifications WHERE channel = 'telegram'")

    # Orphaned tool_definitions rows for the removed communication tools.
    op.execute(
        "DELETE FROM tool_definitions WHERE name IN ('send_telegram', 'send_approval_prompt')"
    )

    # Retag telegram sessions back to web — do NOT delete the session row.
    op.execute("UPDATE sessions SET surface = 'web' WHERE surface = 'telegram'")


def downgrade() -> None:
    # Irreversible: the deleted conversation/notification/tool rows are gone, and
    # the original surface of retagged sessions is not recorded. No-op by design.
    pass
