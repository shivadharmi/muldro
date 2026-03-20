"""ServiceContainer — typed container replacing untyped services dict."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.services.artifact_store import ArtifactStore
    from src.services.audit import AuditService
    from src.services.event_processor import EventProcessor
    from src.services.goal_tracker import GoalTracker
    from src.services.governor import Governor
    from src.services.graph_executor import GraphExecutor
    from src.services.memory_service import MemoryService
    from src.services.notifier import Notifier
    from src.services.oauth_manager import OAuthManager
    from src.services.operator import Operator
    from src.services.planner import Planner
    from src.services.presenter import Presenter
    from src.services.procedure_library import ProcedureLibrary
    from src.services.search_service import SearchService
    from src.services.vector_store import VectorStore
    from src.services.working_memory import WorkingMemoryService
    from src.services.world_model import WorldModel


@dataclass
class ServiceContainer:
    """Typed container for all Jarvis services.

    Replaces the untyped ``services: dict`` that was passed around.
    All fields are optional so the orchestrator degrades gracefully
    when a service fails to initialise.
    """

    event_processor: EventProcessor | None = None
    world_model: WorldModel | None = None
    memory_service: MemoryService | None = None
    planner: Planner | None = None
    governor: Governor | None = None
    presenter: Presenter | None = None
    audit: AuditService | None = None
    vector_store: VectorStore | None = None
    search_service: SearchService | None = None
    working_memory: WorkingMemoryService | None = None
    goal_tracker: GoalTracker | None = None
    procedure_library: ProcedureLibrary | None = None
    artifact_store: ArtifactStore | None = None
    oauth_manager: OAuthManager | None = None
    notifier: Notifier | None = None
    graph_executor: GraphExecutor | None = None
    operator: Operator | None = None

    # Catch-all for services not yet promoted to a typed field.
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ServiceContainer":
        """Build a ServiceContainer from a legacy services dict."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values() if f.name != "extras"}
        kwargs: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for key, val in d.items():
            if key in known_fields:
                kwargs[key] = val
            elif key == "memory":
                # Legacy alias — map to memory_service if not already set
                kwargs.setdefault("memory_service", val)
            else:
                extras[key] = val
        kwargs["extras"] = extras
        return cls(**kwargs)

    # ── dict compatibility layer (for intelligence_server.py) ──────────
    def __getitem__(self, key: str) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        if key in self.extras:
            return self.extras[key]
        # Legacy alias
        if key == "memory":
            return self.memory_service
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
