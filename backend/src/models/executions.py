from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Execution(Base, TimestampMixin):
    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, running, awaiting_approval, completed, failed, cancelled
    current_task_id: Mapped[str | None] = mapped_column(String(64))
    errors: Mapped[dict | None] = mapped_column(JSONB)
    audit_ref: Mapped[str | None] = mapped_column(String(128))

    task_runs: Mapped[list["ExecutionTaskRun"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )


class ExecutionTaskRun(Base, TimestampMixin):
    __tablename__ = "execution_task_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("executions.execution_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, running, completed, failed
    artifact_ref: Mapped[str | None] = mapped_column(String(512))
    result_data: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)

    execution: Mapped["Execution"] = relationship(back_populates="task_runs")
