"""Tests for TaskService — CRUD, state machine, and dependency tracking."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.task_service import ALLOWED_TRANSITIONS, TaskService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.agent_stream = MagicMock(return_value="jarvis:agents:usr_1")
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def service(mock_db, mock_event_bus):
    return TaskService(mock_db, event_bus=mock_event_bus)


@pytest.fixture
def service_no_bus(mock_db):
    return TaskService(mock_db)


def _make_task(
    task_id="task_001",
    user_id="usr_1",
    title="Test task",
    status="created",
    goal_id=None,
    task_type="general",
    priority="medium",
):
    t = MagicMock()
    t.task_id = task_id
    t.user_id = user_id
    t.title = title
    t.status = status
    t.goal_id = goal_id
    t.task_type = task_type
    t.priority = priority
    t.created_at = MagicMock()
    return t


def _make_dep(
    task_id="task_002",
    depends_on_task_id="task_001",
    dependency_type="blocks",
):
    d = MagicMock()
    d.task_id = task_id
    d.depends_on_task_id = depends_on_task_id
    d.dependency_type = dependency_type
    return d


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_task(self, service, mock_db):
        with patch("src.services.task_service.ULID") as mock_ulid:
            mock_ulid.return_value = "01HTEST"
            task = await service.create_task(
                user_id="usr_1",
                title="Draft investor update",
                description="Weekly update email",
                task_type="draft_email",
                priority="high",
            )
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        assert task.task_id == "task_01HTEST"
        assert task.status == "created"

    @pytest.mark.asyncio
    async def test_create_task_publishes_event(self, service, mock_event_bus):
        with patch("src.services.task_service.ULID"):
            await service.create_task(user_id="usr_1", title="Test")
        mock_event_bus.publish.assert_called_once()
        call_args = mock_event_bus.publish.call_args
        assert call_args[0][1] == "task.created"

    @pytest.mark.asyncio
    async def test_create_task_no_bus(self, service_no_bus, mock_db):
        with patch("src.services.task_service.ULID"):
            task = await service_no_bus.create_task(user_id="usr_1", title="No bus task")
        assert task is not None
        mock_db.add.assert_called_once()


class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_tasks_with_filters(self, service, mock_db):
        t1 = _make_task(task_id="task_001", status="created")
        t2 = _make_task(task_id="task_002", status="created")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [t1, t2]
        mock_db.execute = AsyncMock(return_value=result_mock)

        tasks = await service.list_tasks(
            user_id="usr_1",
            status="created",
            task_type="general",
            priority="medium",
        )
        assert len(tasks) == 2
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        tasks = await service.list_tasks(user_id="usr_1")
        assert tasks == []


class TestTransition:
    @pytest.mark.asyncio
    async def test_transition_valid(self, service, mock_db):
        task = _make_task(status="created")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.transition("task_001", "usr_1", "queued")
        assert result.status == "queued"
        mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_transition_invalid_raises(self, service, mock_db):
        task = _make_task(status="completed")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="Invalid transition"):
            await service.transition("task_001", "usr_1", "executing")

    @pytest.mark.asyncio
    async def test_transition_not_found_raises(self, service, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="Task not found"):
            await service.transition("task_missing", "usr_1", "queued")

    @pytest.mark.asyncio
    async def test_transition_publishes_event(self, service, mock_db, mock_event_bus):
        task = _make_task(status="created")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        mock_db.execute = AsyncMock(return_value=result_mock)

        await service.transition("task_001", "usr_1", "queued")
        mock_event_bus.publish.assert_called_once()
        payload = mock_event_bus.publish.call_args[0][2]
        assert payload["old_status"] == "created"
        assert payload["new_status"] == "queued"

    def test_all_statuses_in_transition_map(self):
        expected = {
            "created",
            "queued",
            "planning",
            "executing",
            "awaiting_approval",
            "awaiting_input",
            "completed",
            "failed",
            "cancelled",
            "blocked",
        }
        assert set(ALLOWED_TRANSITIONS.keys()) == expected


class TestDependencies:
    @pytest.mark.asyncio
    async def test_add_dependency(self, service, mock_db):
        # _would_create_cycle returns False (no cycle)
        cycle_result = MagicMock()
        cycle_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=cycle_result)

        await service.add_dependency("task_002", "task_001", "blocks")
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_cycle_detection_self_ref(self, service, mock_db):
        with pytest.raises(ValueError, match="cycle"):
            await service.add_dependency("task_001", "task_001", "blocks")

    @pytest.mark.asyncio
    async def test_cycle_detection_indirect(self, service, mock_db):
        """A -> B -> A should be detected as a cycle."""
        # When checking if adding A depends_on B creates cycle,
        # we traverse from B's deps. B depends on A => cycle.
        cycle_result = MagicMock()
        # B's dependency: B -> A (task_id=B, depends_on=A)
        cycle_result.all.return_value = [("task_A",)]
        mock_db.execute = AsyncMock(return_value=cycle_result)

        with pytest.raises(ValueError, match="cycle"):
            await service.add_dependency("task_A", "task_B", "blocks")


class TestCompleteUnblocksDependents:
    @pytest.mark.asyncio
    async def test_complete_unblocks_dependents(self, service, mock_db):
        """Completing a task unblocks blocked dependents."""
        # Setup: task_001 (executing), task_002 (blocked, depends on 001)
        task_001 = _make_task(task_id="task_001", status="executing")
        task_002 = _make_task(task_id="task_002", status="blocked")
        dep = _make_dep(
            task_id="task_002",
            depends_on_task_id="task_001",
            dependency_type="blocks",
        )

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()

            # Call 1: get_task for transition (task_001)
            if call_count == 1:
                result.scalar_one_or_none.return_value = task_001
                return result

            # Call 2: _unblock_dependents query for deps
            if call_count == 2:
                result.scalars.return_value.all.return_value = [dep]
                return result

            # Call 3: get_task for dependent (task_002)
            if call_count == 3:
                result.scalar_one_or_none.return_value = task_002
                return result

            # Call 4: get_dependencies for task_002
            if call_count == 4:
                result.scalars.return_value.all.return_value = [dep]
                return result

            # Call 5: get_task for blocker check (task_001)
            if call_count == 5:
                completed_001 = _make_task(task_id="task_001", status="completed")
                result.scalar_one_or_none.return_value = completed_001
                return result

            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        await service.complete_task("task_001", "usr_1")

        # task_002 should have been unblocked to "queued"
        assert task_002.status == "queued"
