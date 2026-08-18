"""6C wiring regression: the deep write_lock + RiskAssessor must source their Redis from
``services.extras['redis']`` — the accessor the rest of the codebase uses (runtime.py stores
it there; perception_tick/routes_chat/event_publisher read it there).

``ServiceContainer`` has NO typed ``redis`` field, so the old ``getattr(services, 'redis',
None)`` form in ``_build_deep_agent_for`` was ALWAYS None on the deep path. Consequences:
* the cross-path write_lock (6C) got ``redis=None`` → ``write_lock`` fell through → the lock
  SILENTLY never engaged;
* the RiskAssessor got ``redis=None`` → risk assessment never used its 24h cache.

These tests build a deep agent through the LIVE seam (``call_agent_stream`` deep branch →
``_build_deep_agent_for``) with a ``ServiceContainer`` whose ``extras['redis']`` is a sentinel,
capture the ``redis=`` reaching BOTH consumers, and assert the sentinel arrives (not None).
They FAIL against the ``getattr`` form (both get None) and PASS once redis is sourced from
``services.extras``.

The librarian's ``InteractionLearner(redis=...)`` is DELIBERATELY excluded — it intentionally
mirrors the live muldro.py construction (``redis=None``) and is proven separately in
tests/test_agent_invoker_deep_hardening.py::test_librarian_learn_closure_adapts_interaction_learner.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.orchestrator.services import ServiceContainer
from tests.conftest import make_mock_settings

SENTINEL_REDIS = object()


def _make_invoker(services) -> AgentInvoker:
    """A real AgentInvoker reaching the runtime=deep branch. ``tools_override=[]``
    short-circuits ``_resolve_tools``; ``assemble_context`` returns ``""``. ``services`` is
    the container under test (its ``extras['redis']`` is what must reach the middleware)."""
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(
        name="executor", prompt="p", model_tier="sonnet", capability_scope={"email.send"}
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_inline_format=False),
        client=MagicMock(),
        services=services,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={"executor": agent},
        checkpointer_provider=lambda: object(),
    )


async def _fake_adapter(*a, **k):
    yield {
        "event": "agent_done",
        "agent": "executor",
        "text": "ok",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "tools_called": [],
        "latency_ms": 1,
        "cost_usd": 0.0,
    }


async def _build_and_capture(invoker) -> dict:
    """Drive the deep branch of ``call_agent_stream`` far enough to construct the middleware
    chain, capturing the write_lock ``redis=`` kwarg and the trust_gate ``assess_risk`` closure.

    ``build_deep_agent`` + ``stream_deep_agent_events`` are faked so no real model/DB is needed;
    the middleware factories are spied so their construction-time kwargs are observable. The
    ``redis`` value reaching each consumer is evaluated by the REAL ``_build_deep_agent_for``
    body (write_lock at build time, the risk closure at call time) — that is exactly the seam
    under test."""
    captured: dict = {}

    def _cap_write_lock(**kw):
        captured["write_lock_redis"] = kw["redis"]
        return object()

    def _cap_trust_gate(**kw):
        captured["assess_risk"] = kw["assess_risk"]
        return object()

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_muldro_tool_dispatcher", return_value=object()),
        patch(
            "src.orchestrator.agent_invoker.make_governor_audit_middleware", return_value=object()
        ),
        patch(
            "src.orchestrator.agent_invoker.make_write_lock_middleware", side_effect=_cap_write_lock
        ),
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware", side_effect=_cap_trust_gate
        ),
        patch(
            "src.orchestrator.agent_invoker.make_librarian_extract_middleware",
            return_value=object(),
        ),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
    ):
        _ = [
            f
            async for f in invoker.call_agent_stream(
                "executor",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]
    return captured


async def test_write_lock_redis_sourced_from_services_extras():
    """The deep write_lock is built with ``redis=services.extras['redis']`` (the sentinel),
    NOT ``getattr(services,'redis',None)`` (always None) — else the 6C cross-path lock never
    engages."""
    invoker = _make_invoker(ServiceContainer(extras={"redis": SENTINEL_REDIS}))
    captured = await _build_and_capture(invoker)

    assert captured["write_lock_redis"] is SENTINEL_REDIS, (
        "deep write_lock must receive services.extras['redis']; got "
        f"{captured['write_lock_redis']!r} — getattr(services,'redis') is always None "
        "(ServiceContainer has no typed redis field), silently disabling the write lock"
    )


async def test_risk_assessor_redis_sourced_from_services_extras():
    """The deep RiskAssessor closure calls ``get_or_assess_risk`` with
    ``redis=services.extras['redis']`` (the sentinel) so the 24h risk cache is actually used —
    NOT ``getattr(services,'redis',None)`` (always None → never caches)."""
    invoker = _make_invoker(ServiceContainer(extras={"redis": SENTINEL_REDIS}))
    captured = await _build_and_capture(invoker)

    assess_risk = captured["assess_risk"]
    with patch(
        "src.services.risk_assessor.get_or_assess_risk",
        new=AsyncMock(
            return_value=SimpleNamespace(risk_level="low", reasoning="x", reversible=True)
        ),
    ) as mock_assess:
        await assess_risk("email.send", {"to": "a@b.com"})

    assert mock_assess.await_args.kwargs["redis"] is SENTINEL_REDIS, (
        "deep RiskAssessor must receive services.extras['redis'] for its 24h cache; got "
        f"{mock_assess.await_args.kwargs['redis']!r} — getattr(services,'redis') is always "
        "None so risk assessment never cached"
    )
