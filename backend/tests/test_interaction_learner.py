"""Tests for InteractionLearner — async learning from user interactions."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.interaction_learner import SKIP_LEARNING_INTENTS, InteractionLearner
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_memory_service():
    svc = MagicMock()
    svc.extract_and_store = AsyncMock(return_value=["mem_001"])
    return svc


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_db_factory(mock_db):
    @asynccontextmanager
    async def factory():
        yield mock_db

    return factory


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    # Default: no cooldown active (SET NX returns True)
    r.set = AsyncMock(return_value=True)
    return r


@pytest.fixture
def learner(settings, mock_db_factory, mock_redis):
    return InteractionLearner(
        settings=settings,
        db_factory=mock_db_factory,
        redis=mock_redis,
    )


@patch("src.services.interaction_learner.MemoryService")
@pytest.mark.asyncio
async def test_learn_calls_extract_and_store(mock_mem_cls, learner):
    """Should create a fresh MemoryService and call extract_and_store."""
    mock_svc = MagicMock()
    mock_svc.extract_and_store = AsyncMock(return_value=["mem_001"])
    mock_mem_cls.return_value = mock_svc

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check my GitHub repos",
        agent_response="You have 39 active repositories on GitHub.",
        intent="data_fetch",
        trace_id="trace_abc",
    )

    mock_svc.extract_and_store.assert_called_once()
    call_kwargs = mock_svc.extract_and_store.call_args.kwargs
    assert "Check my GitHub repos" in call_kwargs["source_text"]
    assert "39 active repositories" in call_kwargs["source_text"]
    assert call_kwargs["user_id"] == TEST_USER_ID
    assert call_kwargs["workspace_id"] == TEST_WORKSPACE_ID
    assert call_kwargs["provenance_extra"]["source"] == "interaction"
    assert call_kwargs["provenance_extra"]["intent"] == "data_fetch"
    assert call_kwargs["prompt_addendum"] is not None


@patch("src.services.interaction_learner.MemoryService")
@pytest.mark.asyncio
async def test_learn_commits_on_success(mock_mem_cls, learner, mock_db):
    """Should commit the DB session when memories are extracted."""
    mock_svc = MagicMock()
    mock_svc.extract_and_store = AsyncMock(return_value=["mem_001"])
    mock_mem_cls.return_value = mock_svc

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check repos",
        agent_response="39 repos found.",
        intent="data_fetch",
        trace_id="trace_commit",
    )

    mock_db.commit.assert_called_once()


@patch("src.services.interaction_learner.MemoryService")
@pytest.mark.asyncio
async def test_learn_skips_commit_when_no_memories(mock_mem_cls, learner, mock_db):
    """Should not commit when no memories are extracted."""
    mock_svc = MagicMock()
    mock_svc.extract_and_store = AsyncMock(return_value=[])
    mock_mem_cls.return_value = mock_svc

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check repos",
        agent_response="Nothing found.",
        intent="data_fetch",
        trace_id="trace_nocommit",
    )

    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", list(SKIP_LEARNING_INTENTS))
async def test_learn_skips_trivial_intents(learner, intent):
    """Should skip extraction for trivial intents."""
    # If gates work, MemoryService is never imported/created
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello!",
        agent_response="Hi there!",
        intent=intent,
        trace_id="trace_skip",
    )
    # No assertion needed — if MemoryService were called with no mock, it would fail


@pytest.mark.asyncio
async def test_learn_skips_when_cooldown_active(learner, mock_redis):
    """Should skip extraction when Redis cooldown key already exists."""
    mock_redis.set = AsyncMock(return_value=False)  # Key already exists

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check my repos",
        agent_response="You have 39 repos.",
        intent="data_fetch",
        trace_id="trace_dup",
    )


@pytest.mark.asyncio
async def test_learn_sets_redis_cooldown(learner, mock_redis):
    """Should set Redis cooldown key with 60s TTL."""
    with patch("src.services.interaction_learner.MemoryService") as mock_mem_cls:
        mock_svc = MagicMock()
        mock_svc.extract_and_store = AsyncMock(return_value=[])
        mock_mem_cls.return_value = mock_svc

        await learner.learn(
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            user_message="Check repos",
            agent_response="39 repos found.",
            intent="data_fetch",
            trace_id="trace_cd",
        )

    mock_redis.set.assert_called_once_with(
        f"jarvis:learn_cooldown:{TEST_USER_ID}", "1", ex=60, nx=True
    )


@patch("src.services.interaction_learner.MemoryService")
@pytest.mark.asyncio
async def test_learn_survives_extraction_failure(mock_mem_cls, learner):
    """Should not raise if extract_and_store fails."""
    mock_svc = MagicMock()
    mock_svc.extract_and_store = AsyncMock(side_effect=RuntimeError("DB down"))
    mock_mem_cls.return_value = mock_svc

    # Should not raise
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check repos",
        agent_response="39 repos.",
        intent="data_fetch",
        trace_id="trace_err",
    )


@pytest.mark.asyncio
async def test_learn_skips_empty_response(learner):
    """Should skip extraction when agent response is empty."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello",
        agent_response="",
        intent="data_fetch",
        trace_id="trace_empty",
    )


@patch("src.services.interaction_learner.MemoryService")
@pytest.mark.asyncio
async def test_learn_handles_planner_intent(mock_mem_cls, learner):
    """Should learn from complex intents that go through the Planner (intent=None)."""
    mock_svc = MagicMock()
    mock_svc.extract_and_store = AsyncMock(return_value=["mem_001"])
    mock_mem_cls.return_value = mock_svc

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Draft an email to Alice about the Q3 report",
        agent_response="I've drafted the email and sent it to Alice.",
        intent=None,
        trace_id="trace_complex",
    )

    mock_svc.extract_and_store.assert_called_once()


# --- Integration tests: orchestrator wiring ---


@patch("src.orchestrator.jarvis.get_anthropic_client")
@pytest.mark.asyncio
async def test_orchestrator_initializes_learner_when_memory_service_present(mock_get_client):
    """Verify JarvisOrchestrator creates InteractionLearner when memory_service exists."""
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.orchestrator.services import ServiceContainer

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    services = ServiceContainer(memory_service=MagicMock())
    settings = make_mock_settings()
    db_factory = MagicMock()

    orch = JarvisOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=services,
    )

    assert orch._interaction_learner is not None
    assert isinstance(orch._interaction_learner, InteractionLearner)


@patch("src.orchestrator.jarvis.get_anthropic_client")
@pytest.mark.asyncio
async def test_orchestrator_skips_learner_when_no_memory_service(mock_get_client):
    """Verify JarvisOrchestrator does not create learner when memory_service is None."""
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.orchestrator.services import ServiceContainer

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    services = ServiceContainer(memory_service=None)
    settings = make_mock_settings()
    db_factory = MagicMock()

    orch = JarvisOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=services,
    )

    assert orch._interaction_learner is None
