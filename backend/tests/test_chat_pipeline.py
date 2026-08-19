"""Tests for ``src.orchestrator.chat_pipeline``.

The prompt-builder tests that used to live here (``format_prior_step_results``,
``format_prior_results_for_presenter``, ``build_user_action_block``) went with the
legacy multi-agent arm: there is no Presenter step to build a prompt for and no
prior-step results to thread between agents. What remains is the user/lead split.
"""

import types

import pytest


def _step(**kw):
    """A lightweight stand-in for PlanStep (the helper only reads attributes)."""
    return types.SimpleNamespace(**kw)


class TestResolvePlanRouting:
    """``resolve_plan_routing`` returns ONLY the user-actor steps, in plan order.

    Everything else is the lead's — it is built with the plan's capability union and
    discovers its own tools, so no step is pre-resolved to an agent or a tool set.
    """

    def test_returns_only_user_actor_steps_in_plan_order(self):
        from src.orchestrator.chat_pipeline import resolve_plan_routing

        steps = [
            _step(actor="user", capability="email.draft"),
            _step(actor="muldro", capability="system.respond"),
            _step(actor="muldro", capability="email.send"),
            _step(actor="user", capability="approve.manual"),
        ]
        assert resolve_plan_routing(steps) == [steps[0], steps[3]]

    def test_no_user_steps_returns_empty(self):
        from src.orchestrator.chat_pipeline import resolve_plan_routing

        steps = [
            _step(actor="muldro", capability="calendar.read"),
            _step(actor="muldro", capability="knowledge.search"),
        ]
        assert resolve_plan_routing(steps) == []

    def test_empty_plan_returns_empty(self):
        from src.orchestrator.chat_pipeline import resolve_plan_routing

        assert resolve_plan_routing([]) == []

    def test_takes_no_db_and_does_no_io(self):
        """It is a pure filter — a sync function with one argument. If this signature
        grows a session factory again, per-step resolution has crept back in."""
        import inspect

        from src.orchestrator.chat_pipeline import resolve_plan_routing

        assert not inspect.iscoroutinefunction(resolve_plan_routing)
        assert list(inspect.signature(resolve_plan_routing).parameters) == ["steps"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
