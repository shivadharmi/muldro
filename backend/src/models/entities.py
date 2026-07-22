from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Entity(Base, TimestampMixin):
    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # person, organization, project, goal, task, meeting, document, message_thread, note, decision
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False)
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    source_refs: Mapped[dict | None] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0)
    search_vector = mapped_column(TSVECTOR, nullable=True)

    aliases: Mapped[list["EntityAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_entities_user_type_name", "user_id", "entity_type", "canonical_name"),
        Index("ix_entities_search_vector", "search_vector", postgresql_using="gin"),
        # One entity per (user, workspace, type, name). Entity identity is per-user — reads and
        # upserts filter by user_id AND workspace_id — so the key must include user_id, else a
        # second user in the same workspace can't own an entity another user already named
        # (the workspace-scoped constraint rejected the insert and the user-scoped retry could
        # not resolve the other user's row). See migration 8bed72861ada.
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "entity_type",
            "canonical_name",
            name="uq_entities_user_ws_type_name",
        ),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), default="name")  # name, email, handle

    entity: Mapped["Entity"] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("ix_aliases_entity", "entity_id"),
        Index("ix_aliases_lookup", "alias"),
        # A strong identifier (email/handle) maps to exactly ONE entity per workspace —
        # this is what actually prevents the shared-alias duplication. Name-type aliases
        # legitimately collide (many "John"s) and are intentionally left unconstrained.
        Index(
            "uq_aliases_strong_ident",
            "workspace_id",
            "alias",
            unique=True,
            postgresql_where=text("alias_type IN ('email', 'handle')"),
        ),
    )


class EntityRelationship(Base, TimestampMixin):
    __tablename__ = "entity_relationships"

    relation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    from_entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # owns, works_on, related_to, blocks, supports,
    # requested_by, scheduled_with, interested_in, reports_to
    to_entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    strength: Mapped[float] = mapped_column(Float, default=1.0)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_relations_from", "from_entity_id", "relation_type"),
        Index("ix_relations_to", "to_entity_id", "relation_type"),
        Index("ix_entity_rels_ws", "workspace_id"),
    )


class EntityFact(Base, TimestampMixin):
    """Bi-temporal attribute belief — one row per (entity, attr_key) assertion.

    Superseding a contradicting value closes the old row (``valid_to = now``,
    ``superseded_by = new_fact_id``) and inserts a new current row, so the full
    history is queryable as-of (spec §4.6 items 3-5). ``entities.attributes`` JSONB
    remains the denormalized *current* snapshot; these rows are the versioned truth.

    ``confidence`` stores the AGE-0 evidence base (``1 - (1 - reliability)^n``); the
    presented, age-decayed value is computed live at read time (see
    ``entity_facts.confidence.current_confidence``). ``attr_value`` holds the raw
    JSON-serialisable value (scalar or nested)."""

    __tablename__ = "entity_facts"

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attr_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attr_value = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    corroboration_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_entity_facts_lookup", "entity_id", "attr_key", "valid_to"),
        Index("ix_entity_facts_ws", "workspace_id"),
        # At most ONE current (valid_to IS NULL) fact per (entity, attr_key). Prevents a
        # concurrent race from inserting two current rows (which makes current_fact()'s
        # scalar_one_or_none() raise MultipleResultsFound). See migration 8129484eed6f.
        Index(
            "uq_entity_facts_current",
            "entity_id",
            "attr_key",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
