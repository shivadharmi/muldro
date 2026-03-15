"""Artifacts — S3-backed document/file storage with metadata."""

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # document, email, screenshot, output, attachment
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    entity_links: Mapped[list | None] = mapped_column(ARRAY(String(64)), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (Index("ix_artifacts_user_type", "user_id", "artifact_type"),)
