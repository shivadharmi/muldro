"""Remove the Playwright browser stack.

The browser-automation MCP server is gone from the catalog and the seed lists, but neither
``ToolRegistry.seed_defaults`` nor ``seed_installations`` has a delete path — both upsert
and never prune. Without this migration every existing database keeps:

  * 22 ``browser_*`` rows in ``tool_definitions``, still resolvable by capability and still
    offerable to an agent, routing to a server that no longer spawns;
  * a ``playwright`` row in ``integration_installations`` whose command is an ``npx``
    package the runtime will never invoke again;
  * a ``playwright`` T1 row in ``server_trust_records``.

It also drops ``browser_actions``, the action-replay audit table for the direct Playwright
driver deleted in ae3ab1b. Nothing has written to it since that driver was removed and
nothing read from it before — the model was defined and exported, never used.

Data-only deletes are scoped by the exact server/tool names this repo seeded, so an
operator's own rows under a different server name are untouched.

Revision ID: c1a7f0e9d4b2
Revises: 877e3d55fc30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1a7f0e9d4b2"
down_revision = "877e3d55fc30"
branch_labels = None
depends_on = None

# The exact tool names seeded for the playwright server, pinned here rather than imported
# from the catalog: the catalog no longer contains them, and a migration must describe the
# state of the database it is migrating, not the state of today's code.
_BROWSER_TOOL_NAMES = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_tabs",
    "browser_press_key",
    "browser_select_option",
    "browser_hover",
    "browser_drag",
    "browser_handle_dialog",
    "browser_file_upload",
    "browser_close",
    "browser_resize",
    "browser_network_requests",
    "browser_console_messages",
    "browser_evaluate",
    "browser_run_code",
    "browser_install",
    "browser_navigate_back",
    "browser_take_screenshot",
    "browser_wait_for",
    "browser_fill_form",
)


def upgrade() -> None:
    bind = op.get_bind()

    # Delete by name AND server so a same-named tool an operator mapped to a different
    # server survives. Both conditions were true for every row this repo seeded.
    bind.execute(
        sa.text(
            "DELETE FROM tool_definitions "
            "WHERE server = 'playwright' AND name = ANY(:names)"
        ).bindparams(sa.bindparam("names", value=list(_BROWSER_TOOL_NAMES)))
    )
    bind.execute(sa.text("DELETE FROM integration_installations WHERE server_name = 'playwright'"))
    bind.execute(sa.text("DELETE FROM server_trust_records WHERE server_name = 'playwright'"))

    # Postgres drops a table's own indexes with it, so the two indexes the initial schema
    # created (ix_browser_actions_session, ix_browser_actions_session_id) need no explicit
    # drop — naming them here would only add two ways for this to fail on a drifted DB.
    op.drop_table("browser_actions")


def downgrade() -> None:
    """Recreate the table only.

    The deleted seed rows are deliberately not restored: re-seeding is what
    ``seed_defaults`` / ``seed_installations`` do at startup, and they can only put back
    what the catalog still contains. Restoring rows for a server the code can no longer
    spawn would recreate exactly the orphan state this migration exists to clear.
    """
    op.create_table(
        "browser_actions",
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("selector", sa.Text(), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("result_status", sa.String(length=20), nullable=False),
        sa.Column("screenshot_before", sa.String(length=64), nullable=True),
        sa.Column("screenshot_after", sa.String(length=64), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index("ix_browser_actions_session", "browser_actions", ["session_id", "created_at"])
    op.create_index("ix_browser_actions_session_id", "browser_actions", ["session_id"])
