"""WorkflowContext — typed container replacing untyped context dicts in workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.orchestrator.services import ServiceContainer


@dataclass
class WorkflowContext:
    """Typed context passed between workflow steps.

    Shared fields are typed attributes. Step-specific outputs are stored
    in ``data`` and merged after each step completes.
    """

    user_id: str
    workspace_id: str = ""
    settings: Settings | None = None
    services: ServiceContainer | None = None
    credentials: dict[str, Any] = field(default_factory=dict)

    # Step-specific outputs — merged after each step via update()
    data: dict[str, Any] = field(default_factory=dict)

    def update(self, result: dict[str, Any]) -> None:
        """Merge step output into data."""
        self.data.update(result)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from data (step outputs)."""
        return self.data.get(key, default)

    @classmethod
    def from_params(cls, user_id: str, params: dict[str, Any] | None = None) -> WorkflowContext:
        """Create context from workflow run params."""
        p = params or {}
        return cls(
            user_id=user_id,
            workspace_id=p.pop("workspace_id", ""),
            settings=p.pop("settings", None),
            services=p.pop("services", None),
            credentials=p.pop("credentials", {}),
            data=p,
        )
