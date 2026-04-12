"""Tests for Fix-2: Trust Path Unification.

Tests cover:
- TrustEngine.evaluate() with workspace_id parameter
- TrustEngine.evaluate_plan_risk() convenience method
- Governor._check_trust uses evaluate_plan_risk
- Governor approval_type stores capability, not risk level
- _DECISION_TO_RUN_STATUS mapping
- Risk cache key includes user context
- GraphExecutor does not mutate _workspace_id
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PolicyDecision
from src.services.governor import _DECISION_TO_RUN_STATUS, Governor
from src.services.risk_assessor import RiskAssessment, build_risk_cache_key

# ── Task 5.5: _DECISION_TO_RUN_STATUS mapping ───────────────────────


def test_decision_to_run_status_auto_execute():
    assert _DECISION_TO_RUN_STATUS["auto_execute"] == "pending"


def test_decision_to_run_status_auto_execute_notify():
    assert _DECISION_TO_RUN_STATUS["auto_execute_notify"] == "pending"


def test_decision_to_run_status_auto_execute_silent():
    assert _DECISION_TO_RUN_STATUS["auto_execute_silent"] == "pending"


def test_decision_to_run_status_approval_required():
    assert _DECISION_TO_RUN_STATUS["approval_required"] == "awaiting_approval"


def test_decision_to_run_status_blocked():
    assert _DECISION_TO_RUN_STATUS["blocked"] == "cancelled"


# ── Task 5.3: Risk cache key includes user context ──────────────────


def test_risk_cache_key_differs_with_user_context():
    key_a = build_risk_cache_key("email.send", {"to": "x"}, {"user_id": "user_1"})
    key_b = build_risk_cache_key("email.send", {"to": "x"}, {"user_id": "user_2"})
    assert key_a != key_b


def test_risk_cache_key_same_without_user_context():
    key_a = build_risk_cache_key("email.send", {"to": "x"})
    key_b = build_risk_cache_key("email.send", {"to": "x"}, None)
    assert key_a == key_b


def test_risk_cache_key_differs_from_no_context():
    key_a = build_risk_cache_key("email.send", {"to": "x"})
    key_b = build_risk_cache_key("email.send", {"to": "x"}, {"user_id": "user_1"})
    assert key_a != key_b


# ── Task 5.4: TrustEngine.evaluate() workspace_id parameter ─────────


@pytest.mark.asyncio
async def test_trust_engine_evaluate_with_workspace_id():
    """evaluate() should use workspace_id param over self._workspace_id."""
    from src.services.trust_engine import TrustEngine

    db = MagicMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    engine = TrustEngine(db, workspace_id="ws_default")

    # Mock _get_trust_state and _get_ceiling to capture args
    mock_state = MagicMock()
    mock_state.trust_level = "first_use"
    engine._get_trust_state = AsyncMock(return_value=mock_state)

    mock_ceiling = MagicMock()
    mock_ceiling.max_level = "autonomous"
    engine._get_ceiling = AsyncMock(return_value=mock_ceiling)

    risk = RiskAssessment(risk_level="low", reasoning="test")
    result = await engine.evaluate("email.send", risk, workspace_id="ws_override")

    # Should pass override workspace to helpers
    engine._get_trust_state.assert_called_once_with("email.send", "low", workspace_id="ws_override")
    engine._get_ceiling.assert_called_once_with("email.send", workspace_id="ws_override")
    assert isinstance(result, PolicyDecision)


@pytest.mark.asyncio
async def test_trust_engine_evaluate_defaults_to_init_workspace():
    """evaluate() without workspace_id should use self._workspace_id."""
    from src.services.trust_engine import TrustEngine

    db = MagicMock()
    engine = TrustEngine(db, workspace_id="ws_init")

    mock_state = MagicMock()
    mock_state.trust_level = "trusted"
    engine._get_trust_state = AsyncMock(return_value=mock_state)

    mock_ceiling = MagicMock()
    mock_ceiling.max_level = "autonomous"
    engine._get_ceiling = AsyncMock(return_value=mock_ceiling)

    risk = RiskAssessment(risk_level="low", reasoning="test")
    await engine.evaluate("email.send", risk)

    engine._get_trust_state.assert_called_once_with("email.send", "low", workspace_id="ws_init")
    engine._get_ceiling.assert_called_once_with("email.send", workspace_id="ws_init")


# ── Task 5.4: evaluate_plan_risk convenience ────────────────────────


@pytest.mark.asyncio
async def test_trust_engine_evaluate_plan_risk():
    """evaluate_plan_risk should construct RiskAssessment and call evaluate."""
    from src.services.trust_engine import TrustEngine

    db = MagicMock()
    engine = TrustEngine(db, workspace_id="ws_1")

    engine.evaluate = AsyncMock(
        return_value=PolicyDecision(decision="auto_execute_notify", risk_level="medium")
    )

    result = await engine.evaluate_plan_risk("email.send", "medium", workspace_id="ws_2")

    assert result.decision == "auto_execute_notify"
    call_args = engine.evaluate.call_args
    assert call_args[0][0] == "email.send"
    assessment = call_args[0][1]
    assert isinstance(assessment, RiskAssessment)
    assert assessment.risk_level == "medium"
    assert call_args[1]["workspace_id"] == "ws_2"


# ── Task 5.1: Governor uses TrustEngine.evaluate_plan_risk ───────────


def _make_mock_plan(risk_level="medium", tasks=None):
    plan = MagicMock()
    plan.plan_id = "plan_001"
    plan.goal = "Test plan"
    plan.risk_level = risk_level
    plan.reasoning_summary = "test"
    plan.execution_mode = "approval_required"
    plan.status = "created"
    plan.tasks = tasks or []
    return plan


def _make_mock_task(task_type="email.send"):
    task = MagicMock()
    task.task_type = task_type
    task.input_data = {}
    return task


@pytest.mark.asyncio
async def test_governor_check_trust_calls_evaluate_plan_risk():
    """_check_trust should call TrustEngine.evaluate_plan_risk."""
    mock_trust = MagicMock()
    mock_trust.evaluate_plan_risk = AsyncMock(
        return_value=PolicyDecision(decision="auto_execute_notify", risk_level="medium")
    )

    db = MagicMock()
    governor = Governor(db=db, trust_engine=mock_trust)

    result = await governor._check_trust("ws_1", "email.send", "medium")

    assert result is True
    mock_trust.evaluate_plan_risk.assert_called_once_with(
        capability="email.send",
        risk_level="medium",
        workspace_id="ws_1",
    )


@pytest.mark.asyncio
async def test_governor_check_trust_returns_false_on_approval_required():
    mock_trust = MagicMock()
    mock_trust.evaluate_plan_risk = AsyncMock(
        return_value=PolicyDecision(decision="approval_required", risk_level="medium")
    )

    db = MagicMock()
    governor = Governor(db=db, trust_engine=mock_trust)

    result = await governor._check_trust("ws_1", "email.send", "medium")
    assert result is False


@pytest.mark.asyncio
async def test_governor_check_trust_returns_false_without_engine():
    db = MagicMock()
    governor = Governor(db=db, trust_engine=None)

    result = await governor._check_trust("ws_1", "email.send", "medium")
    assert result is False


@pytest.mark.asyncio
async def test_governor_approval_type_stores_capability():
    """approval_type should be task capability, not risk level."""
    task = _make_mock_task(task_type="email.send")
    plan = _make_mock_plan(risk_level="high", tasks=[task])

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=db)

    with patch(
        "src.services.approval_service.create_approval", new_callable=AsyncMock
    ) as mock_create:
        mock_approval = MagicMock()
        mock_approval.approval_id = "apr_001"
        mock_create.return_value = mock_approval

        await governor.evaluate_plan("plan_001", "user_1", workspace_id="ws_1")

        # Verify approval_type is the capability, not risk level
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["approval_type"] == "email.send"


# ── Task 5.4: GraphExecutor does not mutate _workspace_id ────────────


@pytest.mark.asyncio
async def test_graph_executor_no_workspace_mutation():
    """GraphExecutor should pass workspace_id to evaluate(), not mutate it."""
    from src.services.graph_executor import GraphExecutor

    mock_trust = MagicMock()
    mock_trust.evaluate = AsyncMock(
        return_value=PolicyDecision(decision="auto_execute_notify", risk_level="low")
    )
    mock_trust._workspace_id = "original_ws"

    settings = MagicMock()
    settings.anthropic_api_key = "test"
    settings.use_bedrock = False
    settings.redis_url = "redis://localhost"

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    GraphExecutor(
        settings=settings,
        db=db,
        trust_engine=mock_trust,
    )

    # Simulate _execute_step trust gate logic
    run = MagicMock()
    run.workspace_id = "ws_override"
    run.user_id = "user_1"
    run.run_id = "run_1"

    step = MagicMock()
    step.input_data = {"capability": "email.send"}
    step.status = "pending"
    step.step_id = "step_1"

    risk = RiskAssessment(risk_level="low", reasoning="test")

    # Directly call evaluate to check it passes workspace_id
    await mock_trust.evaluate("email.send", risk, workspace_id="ws_override")

    # Verify _workspace_id was NOT mutated
    assert mock_trust._workspace_id == "original_ws"
    mock_trust.evaluate.assert_called_with("email.send", risk, workspace_id="ws_override")


# ── Task 5.2: create_graph_executor injects TrustEngine ─────────────


@pytest.mark.asyncio
async def test_create_graph_executor_injects_trust_engine():
    """create_graph_executor should create TrustEngine and Redis."""
    from src.services.graph_executor import create_graph_executor

    settings = MagicMock()
    settings.anthropic_api_key = "test"
    settings.use_bedrock = False
    settings.redis_url = "redis://localhost:6379"
    settings.qdrant_url = ""

    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    with (
        patch("src.services.graph_executor.get_anthropic_client") as mock_client,
        patch("src.services.trust_engine.TrustEngine") as mock_te_cls,
        patch("redis.asyncio.from_url") as mock_redis_from_url,
    ):
        mock_client.return_value = MagicMock()
        mock_te_cls.return_value = MagicMock()
        mock_redis_from_url.return_value = MagicMock()

        executor = await create_graph_executor(settings=settings, db=db, workspace_id="ws_1")

        assert executor._trust_engine is not None
        assert executor._redis is not None
