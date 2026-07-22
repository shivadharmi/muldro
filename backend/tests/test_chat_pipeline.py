"""Characterization tests for the chat orchestration helpers (ORCH-P1-1).

These pin the EXACT behavior of the shared blocks extracted from
``JarvisOrchestrator.process_message`` and ``process_message_stream`` into
``src.orchestrator.chat_pipeline``. The expected strings are copied from the
pre-extraction inline code, so any drift in the moved bodies fails here. They
must stay green across the structural change.
"""

import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _step(**kw):
    """A lightweight stand-in for PlanStep (the helpers only read attributes)."""
    return types.SimpleNamespace(**kw)


class TestFormatPriorStepResults:
    def test_empty_returns_blank(self):
        from src.orchestrator.chat_pipeline import format_prior_step_results

        assert format_prior_step_results({}) == ""

    def test_single_entry(self):
        from src.orchestrator.chat_pipeline import format_prior_step_results

        out = format_prior_step_results({"step_0_perceive": "found 3 emails"})
        assert out == (
            "\n\n--- Prior step results ---\n"
            "[step_0_perceive]:\nfound 3 emails"
            "\n--- End of prior step results ---\n"
        )

    def test_multiple_entries_joined_with_blank_line(self):
        from src.orchestrator.chat_pipeline import format_prior_step_results

        out = format_prior_step_results({"a": "x", "b": "y"})
        assert out == (
            "\n\n--- Prior step results ---\n"
            "[a]:\nx\n\n[b]:\ny"
            "\n--- End of prior step results ---\n"
        )


class TestFormatPriorResultsForPresenter:
    def test_empty_returns_blank(self):
        from src.orchestrator.chat_pipeline import format_prior_results_for_presenter

        assert format_prior_results_for_presenter({}) == ""

    def test_single_entry_uses_presenter_header(self):
        from src.orchestrator.chat_pipeline import format_prior_results_for_presenter

        out = format_prior_results_for_presenter({"step_0_perceive": "data"})
        assert out == (
            "\n\n--- Prior step results (use these to answer the user) ---\n"
            "[step_0_perceive]:\ndata"
            "\n--- End of prior step results ---\n"
        )


class TestBuildUserActionBlock:
    def test_with_context(self):
        from src.orchestrator.chat_pipeline import build_user_action_block

        steps = [_step(description="Reply to email", user_context="urgent")]
        assert (
            build_user_action_block(steps)
            == "\n\nUser actions required:\n- Reply to email (urgent)"
        )

    def test_without_context(self):
        from src.orchestrator.chat_pipeline import build_user_action_block

        steps = [_step(description="Reply to email", user_context=None)]
        assert build_user_action_block(steps) == "\n\nUser actions required:\n- Reply to email"

    def test_multiple_steps_newline_joined(self):
        from src.orchestrator.chat_pipeline import build_user_action_block

        steps = [
            _step(description="A", user_context=None),
            _step(description="B", user_context="ctx"),
        ]
        assert build_user_action_block(steps) == "\n\nUser actions required:\n- A\n- B (ctx)"


class TestResolvePlanRouting:
    def _db_factory(self):
        db = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=db)
        cm.__aexit__ = AsyncMock(return_value=False)
        return MagicMock(return_value=cm)

    async def test_routes_special_capabilities_without_resolver(self, monkeypatch):
        import src.orchestrator.chat_pipeline as cp

        # CapabilityResolver is constructed but only used in the else-branch;
        # route_step must NOT be called for system./reason/respond/perceive.
        monkeypatch.setattr(cp, "CapabilityResolver", lambda db, ws: MagicMock())
        route_step = AsyncMock()
        monkeypatch.setattr(cp, "route_step", route_step)

        steps = [
            _step(actor="user", capability="email.draft"),
            _step(actor="agent", capability="system.respond"),
            _step(actor="agent", capability="reason"),
            _step(actor="agent", capability="respond"),
            _step(actor="agent", capability="perceive"),
        ]
        routing, user_steps = await cp.resolve_plan_routing(self._db_factory(), "ws_1", steps)

        assert len(user_steps) == 1
        assert routing == [
            (steps[1], "", []),
            (steps[2], "presenter", []),
            (steps[3], "presenter", []),
            (steps[4], "perceiver", []),
        ]
        route_step.assert_not_called()

    async def test_resolves_normal_capability_via_route_step(self, monkeypatch):
        import src.orchestrator.chat_pipeline as cp

        resolver = MagicMock()
        resolver.resolve_for_step = AsyncMock(return_value=[{"name": "send_email"}])
        monkeypatch.setattr(cp, "CapabilityResolver", lambda db, ws: resolver)
        monkeypatch.setattr(cp, "route_step", AsyncMock(return_value="executor"))

        steps = [_step(actor="agent", capability="email.send")]
        routing, user_steps = await cp.resolve_plan_routing(self._db_factory(), "ws_1", steps)

        assert user_steps == []
        assert routing == [(steps[0], "executor", [{"name": "send_email"}])]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
