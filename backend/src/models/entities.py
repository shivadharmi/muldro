from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Entity(Base, TimestampMixin):
    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # person, organization, project, goal, task, meeting, document, message_thread, note, decision
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False)
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    source_refs: Mapped[dict | None] = mapped_column(JSONB)

    aliases: Mapped[list["EntityAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_entities_user_type_name", "user_id", "entity_type", "canonical_name"),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
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

    __table_args__ = (
        Index("ix_relations_from", "from_entity_id", "relation_type"),
        Index("ix_relations_to", "to_entity_id", "relation_type"),
    )
