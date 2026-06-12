"""purge legacy jira oauth data

Revision ID: b4e7a1c28f90
Revises: a74c2b19f301
Create Date: 2026-04-21 00:00:00.000000

Purges all OAuth artifacts tied to the legacy `provider='jira'` path so users
re-authorize under the new `atlassian` provider and receive the expanded
Jira + Confluence scope set. The old tokens carry only Jira scopes and would
403 on Confluence tool calls if renamed in place.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b4e7a1c28f90'
down_revision: Union[str, None] = 'a74c2b19f301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop legacy OAuth tokens — they only carry Jira-only scopes.
    # Users will re-consent via the atlassian flow to pick up Confluence scopes.
    op.execute("DELETE FROM oauth_tokens WHERE provider = 'jira'")

    # Drop webhook subscriptions bound to legacy jira tokens (cascade cleanup).
    op.execute("DELETE FROM webhook_subscriptions WHERE provider = 'jira'")

    # Drop orphan IntegrationInstallation rows from the old routes_auth.py
    # bug where _ensure_integration was called with server_name='jira' instead
    # of 'atlassian'. These never had a working token path.
    op.execute("DELETE FROM integration_installations WHERE server_name = 'jira'")

    # Drop perception cursors for the deleted JiraConnector — the connector is
    # gone, so any source='jira' cursor is stale state that would never advance.
    op.execute("DELETE FROM perception_state WHERE source = 'jira'")


def downgrade() -> None:
    # Deletes are not reversible — token material is gone forever.
    # Restoring would require users to re-authorize (which upgrade already forces).
    pass
