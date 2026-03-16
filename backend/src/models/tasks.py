"""Standalone task model with dependency tracking."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    goal_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("goals.goal_id", ondelete="SET NULL")
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("tasks.task_id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(64), default="general")
    source: Mapped[str] = mapped_column(String(32), default="user")
    # user, planner, trigger, workflow
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="created")
    # created, queued, planning, executing, awaiting_approval, awaiting_input,
    # completed, failed, cancelled, blocked
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    assigned_agent: Mapped[str | None] = mapped_column(String(32))

    dependencies: Mapped[list["TaskDependency"]] = relationship(
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_goal", "goal_id"),
        Index("ix_tasks_parent", "parent_task_id"),
    )


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(16), default="blocks")
    # blocks, requires, informs

    task: Mapped["Task"] = relationship(foreign_keys=[task_id], back_populates="dependencies")

    __table_args__ = (
        Index("ix_task_deps_task", "task_id"),
        Index("ix_task_deps_depends_on", "depends_on_task_id"),
    )
