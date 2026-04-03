"""Tests for the ignore decision handling."""

from __future__ import annotations

from src.services.route_resolver import ALWAYS_PRESENT, DEFAULT_ROUTES


class TestIgnoreRouteConfig:
    def test_ignore_not_in_always_present(self):
        """ignore should NOT be in ALWAYS_PRESENT — no Presenter response."""
        assert "ignore" not in ALWAYS_PRESENT

    def test_ignore_route_exists(self):
        """ignore should have a route with empty pipeline."""
        route = next(
            (r for r in DEFAULT_ROUTES if r["decision_type"] == "ignore"),
            None,
        )
        assert route is not None
        assert route["agent_pipeline"] == []

    def test_ignore_route_priority_below_acknowledge(self):
        """ignore route should have lower priority than acknowledge."""
        ignore_route = next(r for r in DEFAULT_ROUTES if r["decision_type"] == "ignore")
        ack_route = next(r for r in DEFAULT_ROUTES if r["decision_type"] == "acknowledge")
        assert ignore_route["priority"] < ack_route["priority"]
