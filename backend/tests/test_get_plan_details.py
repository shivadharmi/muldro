"""Tests for get_plan_details internal MCP tool."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# The implementation function we're testing
from src.tools.intelligence_server import _get_plan_details_impl
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def mock_plan():
    """Create a mock Plan object."""
    plan = MagicMock()
    plan.plan_id = "plan_123"
    plan.workspace_id = TEST_WORKSPACE_ID
    plan.user_id = TEST_USER_ID
    plan.goal = "Build test feature"
    plan.priority = "high"
    plan.risk_level = "medium"
    plan.decision = "plan"
    plan.status = "pending"
    plan.created_at = datetime(2026, 4, 3, 10, 0, tzinfo=timezone.utc)
    plan.tasks = []
    return plan


@pytest.fixture
def mock_plan_with_tasks(mock_plan):
    """Create a mock Plan with tasks."""
    task1 = MagicMock()
    task1.task_id = "task_1"
    task1.task_type = "call_tool"
    task1.description = "Search for relevant data"
    task1.depends_on = []

    task2 = MagicMock()
    task2.task_id = "task_2"
    task2.task_type = "analyze"
    task2.description = "Analyze search results"
    task2.depends_on = ["task_1"]

    mock_plan.tasks = [task1, task2]
    return mock_plan


async def test_plan_found_returns_metadata(mock_db, mock_plan_with_tasks):
    """When plan exists and workspace matches, return full metadata including tasks."""
    # Arrange
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_plan_with_tasks)
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await _get_plan_details_impl(
        plan_id="plan_123",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        db=mock_db,
    )

    # Assert
    assert result["plan_id"] == "plan_123"
    assert result["goal"] == "Build test feature"
    assert result["priority"] == "high"
    assert result["risk_level"] == "medium"
    assert result["decision"] == "plan"
    assert result["status"] == "pending"
    assert result["created_at"] == "2026-04-03T10:00:00+00:00"
    assert len(result["tasks"]) == 2

    # Check task 1
    task1 = result["tasks"][0]
    assert task1["task_id"] == "task_1"
    assert task1["task_type"] == "call_tool"
    assert task1["description"] == "Search for relevant data"
    assert task1["depends_on"] == []

    # Check task 2
    task2 = result["tasks"][1]
    assert task2["task_id"] == "task_2"
    assert task2["task_type"] == "analyze"
    assert task2["description"] == "Analyze search results"
    assert task2["depends_on"] == ["task_1"]


async def test_plan_not_found_returns_not_found(mock_db):
    """When plan_id doesn't exist, return not_found status."""
    # Arrange
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await _get_plan_details_impl(
        plan_id="plan_nonexistent",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        db=mock_db,
    )

    # Assert
    assert result["status"] == "not_found"
    assert "plan_id" not in result or result.get("plan_id") is None


async def test_wrong_workspace_returns_not_found(mock_db, mock_plan):
    """When plan exists but workspace doesn't match, return not_found."""
    # Arrange
    mock_plan.workspace_id = "ws_different"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_plan)
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await _get_plan_details_impl(
        plan_id="plan_123",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,  # Different from plan's workspace
        db=mock_db,
    )

    # Assert
    assert result["status"] == "not_found"
    assert "plan_id" not in result or result.get("plan_id") is None


async def test_plan_with_no_tasks(mock_db, mock_plan):
    """When plan has no tasks, return empty tasks list."""
    # Arrange
    mock_plan.tasks = []
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_plan)
    mock_db.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await _get_plan_details_impl(
        plan_id="plan_123",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        db=mock_db,
    )

    # Assert
    assert result["plan_id"] == "plan_123"
    assert result["status"] == "pending"
    assert result["tasks"] == []
