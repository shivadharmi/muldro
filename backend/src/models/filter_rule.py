"""A filter the founder confirmed: mail matching it is quiet, cheaply.

A rule is not a preference and does not live with them. `set_instruction`
stores prose plus a loose trigger dict, retrieved semantically — the right home
for "I don't much care about receipts" as something muldro remembers. This is a
different kind of thing: it runs on every ingest and every feed build, so it
must be exact, fast and the SAME answer every time. A rule that sometimes
matches is worse than no rule, because nobody can tell whether it fired.

It also needs an IDENTITY, for three jobs prose cannot do:

  * every event it filters is stamped `importance_signals.filtered_by`, so
    "why is this hidden?" has a precise answer rather than a guess;
  * deleting a rule can find exactly the rows it touched and release them —
    without that, a verdict frozen at ingest would outlive the rule that caused
    it and the mail would stay quiet for ever;
  * the founder can list what they have granted, and revoke one of them.

That is the same argument `TrustState` settled the same way: an authority the
founder granted is a row, not a remembered sentence.

`created_from_approval_id` is NOT NULL by construction. A filter rule exists
only because a human confirmed a proposal — muldro never writes one for itself,
and the column is what makes that checkable rather than merely intended.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id

# What a rule may match on. `sender` only, deliberately: it is the shape a
# founder can audit at a glance ("quiet everything from alerts@axisbank.com"),
# and its blast radius is one counterparty. A category rule is far more
# powerful and far harder to reason about — "quiet all financial" would have
# hidden the card alerts triage marked ACTIONABLE. Widen this once rules are
# visible and revocable in the product, not before.
MATCH_KINDS: frozenset[str] = frozenset({"sender"})


class FilterRule(Base, TimestampMixin):
    """One confirmed "keep this quiet" rule, per workspace."""

    __tablename__ = "filter_rules"

    rule_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("fltr")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Which connector the rule applies to. A rule is scoped to a source because
    # "alerts@axisbank.com" means something in gmail and nothing in slack, and
    # an unscoped rule would silently claim authority it was never granted.
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    match_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stored already normalized (lower-cased, address only) so matching is a
    # dict lookup rather than a per-event parse.
    match_value: Mapped[str] = mapped_column(String(320), nullable=False)

    # The approval the founder answered. Not decorative: it is the audit trail
    # for an authority muldro holds, and the reason this column is NOT NULL.
    created_from_approval_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Revocation keeps the row. A deleted rule loses the evidence of what it
    # once hid, and the founder may want to turn one back on.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source",
            "match_kind",
            "match_value",
            name="uq_filter_rule_match",
        ),
        Index("ix_filter_rules_workspace_source", "workspace_id", "source"),
    )
