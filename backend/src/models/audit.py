from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64))
    plan_id: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[str | None] = mapped_column(String(64))
    approval_id: Mapped[str | None] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # plan_created, approval_requested, approval_decided, action_executed, etc.
    summary: Mapped[str | None] = mapped_column(Text)
    artifact_refs: Mapped[dict | None] = mapped_column(JSONB)
    policy_decision: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[dict | None] = mapped_column(JSONB)
