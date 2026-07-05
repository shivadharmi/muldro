from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
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
    )
