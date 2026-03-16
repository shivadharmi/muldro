"""Workflow Registry — register and manage reusable workflows."""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    name: str
    handler: Callable[..., Coroutine[Any, Any, dict]]
    requires_approval: bool = False
    timeout_seconds: int = 300


@dataclass
class Workflow:
    name: str
    description: str
    steps: list[WorkflowStep]
    tags: list[str] = field(default_factory=list)


class WorkflowRegistry:
    """Central registry for all named workflows."""

    def __init__(self):
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        self._workflows[workflow.name] = workflow
        logger.info("Workflow registered: %s (%d steps)", workflow.name, len(workflow.steps))

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def list_workflows(self) -> list[Workflow]:
        return list(self._workflows.values())

    def list_names(self) -> list[str]:
        return list(self._workflows.keys())
