from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_ref: Mapped[str | None] = mapped_column(String(128))
    goal: Mapped[str] = mapped_column(String(256), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    required_context: Mapped[dict | None] = mapped_column(JSONB)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    execution_mode: Mapped[str] = mapped_column(
        String(32), default="approval_required"
    )
    status: Mapped[str] = mapped_column(String(32), default="created")

    tasks: Mapped[list["PlanTask"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_plans_user_created", "user_id", "created_at"),
    )


class PlanTask(Base):
    __tablename__ = "plan_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    depends_on: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="pending")

    plan: Mapped["Plan"] = relationship(back_populates="tasks")
