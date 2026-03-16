"""TaskService — CRUD and state machine for standalone tasks."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.tasks import Task, TaskDependency

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"queued", "planning", "cancelled"},
    "queued": {"planning", "cancelled"},
    "planning": {"executing", "awaiting_approval", "failed", "cancelled"},
    "executing": {"awaiting_approval", "awaiting_input", "completed", "failed", "cancelled"},
    "awaiting_approval": {"executing", "cancelled", "blocked"},
    "awaiting_input": {"executing", "cancelled"},
    "completed": set(),
    "failed": {"queued", "cancelled"},
    "cancelled": set(),
    "blocked": {"queued", "cancelled"},
}


class TaskService:
    """Manage standalone tasks with state machine and dependency tracking."""

    def __init__(self, db: AsyncSession, event_bus=None):
        self._db = db
        self._event_bus = event_bus

    async def create_task(
        self,
        user_id: str,
        title: str,
        description: str | None = None,
        task_type: str = "general",
        source: str = "user",
        priority: str = "medium",
        goal_id: str | None = None,
        parent_task_id: str | None = None,
        due_at: datetime | None = None,
        metadata_json: dict | None = None,
        assigned_agent: str | None = None,
    ) -> Task:
        task_id = f"task_{ULID()}"
        task = Task(
            task_id=task_id,
            user_id=user_id,
            title=title,
            description=description,
            task_type=task_type,
            source=source,
            priority=priority,
            goal_id=goal_id,
            parent_task_id=parent_task_id,
            due_at=due_at,
            metadata_json=metadata_json,
            assigned_agent=assigned_agent,
            status="created",
        )
        self._db.add(task)
        await self._db.flush()

        if self._event_bus:
            await self._event_bus.publish(
                self._event_bus.agent_stream(user_id),
                "task.created",
                {"task_id": task_id, "title": title, "task_type": task_type},
                user_id=user_id,
            )

        logger.info("Task created: %s '%s'", task_id, title)
        return task

    async def get_task(self, task_id: str, user_id: str) -> Task | None:
        result = await self._db.execute(
            select(Task).where(Task.task_id == task_id, Task.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_tasks(
        self,
        user_id: str,
        status: str | None = None,
        goal_id: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        stmt = select(Task).where(Task.user_id == user_id)
        if status:
            stmt = stmt.where(Task.status == status)
        if goal_id:
            stmt = stmt.where(Task.goal_id == goal_id)
        if task_type:
            stmt = stmt.where(Task.task_type == task_type)
        if priority:
            stmt = stmt.where(Task.priority == priority)
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def update_task(self, task_id: str, user_id: str, **kwargs) -> Task | None:
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        for key, value in kwargs.items():
            if hasattr(task, key) and key not in ("task_id", "user_id", "created_at"):
                setattr(task, key, value)
        await self._db.flush()
        return task

    async def transition(self, task_id: str, user_id: str, new_status: str) -> Task:
        """Transition task to a new status, enforcing the state machine."""
        task = await self.get_task(task_id, user_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        allowed = ALLOWED_TRANSITIONS.get(task.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {task.status} -> {new_status} (allowed: {allowed})"
            )

        old_status = task.status
        task.status = new_status
        await self._db.flush()

        if self._event_bus:
            await self._event_bus.publish(
                self._event_bus.agent_stream(user_id),
                "task.status_changed",
                {
                    "task_id": task_id,
                    "old_status": old_status,
                    "new_status": new_status,
                },
                user_id=user_id,
            )

        logger.info("Task %s: %s -> %s", task_id, old_status, new_status)
        return task

    async def start_task(self, task_id: str, user_id: str) -> Task:
        return await self.transition(task_id, user_id, "planning")

    async def cancel_task(self, task_id: str, user_id: str) -> Task:
        return await self.transition(task_id, user_id, "cancelled")

    async def complete_task(self, task_id: str, user_id: str) -> Task:
        """Complete a task and unblock dependents."""
        task = await self.transition(task_id, user_id, "completed")
        await self._unblock_dependents(task_id, user_id)
        return task

    async def add_dependency(
        self,
        task_id: str,
        depends_on_task_id: str,
        dependency_type: str = "blocks",
    ) -> TaskDependency:
        if await self._would_create_cycle(task_id, depends_on_task_id):
            raise ValueError(
                f"Adding dependency would create a cycle: {task_id} -> {depends_on_task_id}"
            )

        dep = TaskDependency(
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
            dependency_type=dependency_type,
        )
        self._db.add(dep)
        await self._db.flush()
        return dep

    async def get_dependencies(self, task_id: str) -> list[TaskDependency]:
        result = await self._db.execute(
            select(TaskDependency).where(TaskDependency.task_id == task_id)
        )
        return list(result.scalars().all())

    async def _would_create_cycle(self, task_id: str, depends_on_id: str) -> bool:
        """Check if adding task_id -> depends_on_id would create a cycle."""
        if task_id == depends_on_id:
            return True

        visited = set()
        stack = [depends_on_id]
        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            result = await self._db.execute(
                select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == current)
            )
            for (dep_id,) in result.all():
                stack.append(dep_id)
        return False

    async def _unblock_dependents(self, completed_task_id: str, user_id: str) -> None:
        """Unblock tasks that were waiting on the completed task."""
        result = await self._db.execute(
            select(TaskDependency).where(
                TaskDependency.depends_on_task_id == completed_task_id,
                TaskDependency.dependency_type == "blocks",
            )
        )
        for dep in result.scalars().all():
            dependent = await self.get_task(dep.task_id, user_id)
            if dependent and dependent.status == "blocked":
                # Check if all blocking deps are now completed
                all_deps = await self.get_dependencies(dep.task_id)
                blocking = [d for d in all_deps if d.dependency_type == "blocks"]
                all_resolved = True
                for b in blocking:
                    blocker = await self.get_task(b.depends_on_task_id, user_id)
                    if blocker and blocker.status != "completed":
                        all_resolved = False
                        break
                if all_resolved:
                    dependent.status = "queued"
                    await self._db.flush()
                    logger.info("Task %s unblocked", dep.task_id)
