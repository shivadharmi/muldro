"""OAuth token storage model.

Stores encrypted OAuth tokens (access + refresh) per provider per user.
Supports automatic refresh detection via expires_at.

Scoping note (TOOL-P2-2): OAuth tokens are intentionally **user-level**, not
workspace-level. A user authorizes a provider (Google/GitHub/Slack/Atlassian)
once; the unique index is `(user_id, provider)`. `workspace_id` records which
workspace first connected the token but is not part of identity — every reader
(`OAuthManager.get_valid_token`, etc.) keys
on `(user_id, provider)` and does not pass a `workspace_id`. Making tokens
per-workspace would force re-authorization in each workspace and require
threading `workspace_id` through all read paths; it is a deliberate feature
decision, not a defect — do not "fix" the index in isolation.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class OAuthToken(Base, TimestampMixin):
    __tablename__ = "oauth_tokens"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # google, github, slack
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    __table_args__ = (Index("ix_oauth_tokens_user_provider", "user_id", "provider", unique=True),)
