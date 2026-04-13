"""Tests for InteractionLearner — async learning from user interactions."""

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
def mock_redis():
    r = AsyncMock()
    # Default: no cooldown active (SET NX returns True)
    r.set = AsyncMock(return_value=True)
    return r


@pytest.fixture
def learner(settings, mock_memory_service, mock_redis):
    return InteractionLearner(
        settings=settings,
        memory_service=mock_memory_service,
        redis=mock_redis,
    )


@pytest.mark.asyncio
async def test_learn_calls_extract_and_store(learner, mock_memory_service):
    """Should call extract_and_store with combined user+agent text."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check my GitHub repos",
        agent_response="You have 39 active repositories on GitHub.",
        intent="data_fetch",
        trace_id="trace_abc",
    )

    mock_memory_service.extract_and_store.assert_called_once()
    call_kwargs = mock_memory_service.extract_and_store.call_args.kwargs
    assert "Check my GitHub repos" in call_kwargs["source_text"]
    assert "39 active repositories" in call_kwargs["source_text"]
    assert call_kwargs["user_id"] == TEST_USER_ID
    assert call_kwargs["workspace_id"] == TEST_WORKSPACE_ID
    assert call_kwargs["provenance_extra"]["source"] == "interaction"
    assert call_kwargs["provenance_extra"]["intent"] == "data_fetch"
    assert call_kwargs["prompt_addendum"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", list(SKIP_LEARNING_INTENTS))
async def test_learn_skips_trivial_intents(learner, mock_memory_service, intent):
    """Should skip extraction for trivial intents: greeting, chitchat, acknowledgment, etc."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello!",
        agent_response="Hi there!",
        intent=intent,
        trace_id="trace_skip",
    )

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_skips_when_cooldown_active(learner, mock_memory_service, mock_redis):
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

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_sets_redis_cooldown(learner, mock_redis):
    """Should set Redis cooldown key with 60s TTL."""
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


@pytest.mark.asyncio
async def test_learn_survives_extraction_failure(learner, mock_memory_service):
    """Should not raise if extract_and_store fails."""
    mock_memory_service.extract_and_store = AsyncMock(side_effect=RuntimeError("DB down"))

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
async def test_learn_skips_empty_response(learner, mock_memory_service):
    """Should skip extraction when agent response is empty."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello",
        agent_response="",
        intent="data_fetch",
        trace_id="trace_empty",
    )

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_handles_planner_intent(learner, mock_memory_service):
    """Should learn from complex intents that go through the Planner (intent=None)."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Draft an email to Alice about the Q3 report",
        agent_response="I've drafted the email and sent it to Alice.",
        intent=None,
        trace_id="trace_complex",
    )

    mock_memory_service.extract_and_store.assert_called_once()


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
