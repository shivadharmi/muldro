"""AgentRoute model — intent-based routing rules for the orchestrator."""

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class AgentRoute(Base, TimestampMixin):
    """Maps intent patterns to agent execution sequences.

    Each route defines:
    - A decision type (from planner output) that triggers this route
    - An ordered list of agents to invoke
    - Optional conditions (JSONB) for fine-grained matching
    - Priority for conflict resolution (higher = checked first)
    """

    __tablename__ = "agent_routes"

    route_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # What decision type this route matches (e.g., "create_task", "ask_user", "research")
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Ordered list of agent names to invoke for this route
    agent_pipeline: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Optional conditions for fine-grained matching (checked against decision dict)
    # e.g., {"source": "email"} or {"has_key": "plan_id"}
    conditions: Mapped[dict | None] = mapped_column(JSONB)

    # Higher priority routes are evaluated first (default 100)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    # Whether this route is active
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Optional: keywords that boost this route during intent matching
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    # Fallback weight — if multiple routes match, higher weight wins
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    __table_args__ = (
        Index("ix_agent_routes_decision_type", "decision_type"),
        Index("ix_agent_routes_enabled", "enabled"),
        Index("ix_agent_routes_priority", "priority"),
    )
