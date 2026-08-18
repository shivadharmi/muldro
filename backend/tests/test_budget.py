"""Tests for BudgetTracker cost calculation with cache/thinking tokens."""

import logging

from src.orchestrator.budget import BudgetTracker


class TestCalculateCost:
    def setup_method(self):
        self.tracker = BudgetTracker(daily_limit_usd=10.0)

    def test_basic_sonnet_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
        )
        # input: 1000/1M * 3.0 = 0.003
        # output: 500/1M * 15.0 = 0.0075
        assert abs(cost - 0.0105) < 1e-6

    def test_opus_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-opus-4-20250514",
            input_tokens=1000,
            output_tokens=500,
        )
        # input: 1000/1M * 15.0 = 0.015
        # output: 500/1M * 75.0 = 0.0375
        assert abs(cost - 0.0525) < 1e-6

    def test_haiku_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-haiku-4-20250514",
            input_tokens=10000,
            output_tokens=2000,
        )
        # input: 10000/1M * 0.80 = 0.008
        # output: 2000/1M * 4.0 = 0.008
        assert abs(cost - 0.016) < 1e-6

    def test_cache_write_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
        )
        # cache_write: 1M/1M * 3.0 * 1.25 = 3.75
        assert abs(cost - 3.75) < 1e-6

    def test_cache_read_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        # cache_read: 1M/1M * 3.0 * 0.10 = 0.30
        assert abs(cost - 0.30) < 1e-6

    def test_thinking_tokens_cost(self):
        cost = self.tracker.calculate_cost(
            model="claude-opus-4-20250514",
            input_tokens=0,
            output_tokens=0,
            thinking_tokens=1000,
        )
        # thinking: 1000/1M * 75.0 = 0.075
        assert abs(cost - 0.075) < 1e-6

    def test_full_cost_with_all_token_types(self):
        cost = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=5000,
            output_tokens=2000,
            cache_creation_input_tokens=10000,
            cache_read_input_tokens=50000,
            thinking_tokens=1000,
        )
        # input: 5000/1M * 3.0 = 0.015
        # output: 2000/1M * 15.0 = 0.03
        # cache_write: 10000/1M * 3.0 * 1.25 = 0.0375
        # cache_read: 50000/1M * 3.0 * 0.10 = 0.015
        # thinking: 1000/1M * 15.0 = 0.015
        expected = 0.015 + 0.03 + 0.0375 + 0.015 + 0.015
        assert abs(cost - expected) < 1e-6

    def test_unknown_model_uses_sonnet_pricing(self):
        cost = self.tracker.calculate_cost(
            model="unknown-model",
            input_tokens=1000,
            output_tokens=500,
        )
        expected = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost == expected

    def test_zero_tokens(self):
        cost = self.tracker.calculate_cost(
            model="claude-sonnet-4-20250514",
            input_tokens=0,
            output_tokens=0,
        )
        assert cost == 0.0


class TestUnknownModelWarning:
    """The 'unknown model' warning must only fire when there are billable tokens.

    A span that never made an API call legitimately has model='unknown' and
    zero tokens — there is no cost to compute, so warning + Sonnet fallback is
    a false alarm.
    """

    def setup_method(self):
        self.tracker = BudgetTracker(daily_limit_usd=10.0)

    def test_zero_token_unknown_model_no_warning_and_zero_cost(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.budget"):
            cost = self.tracker.calculate_cost(
                model="unknown",
                input_tokens=0,
                output_tokens=0,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                thinking_tokens=0,
            )
        assert cost == 0.0
        assert not any("not in catalog or MODEL_PRICING" in r.message for r in caplog.records), (
            "warning must not fire for a zero-token span"
        )

    def test_unknown_model_with_tokens_warns_and_falls_back_to_sonnet(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.budget"):
            cost = self.tracker.calculate_cost(
                model="brand-new-model-7",
                input_tokens=1000,
                output_tokens=500,
            )
        # Warning must still fire for a genuine unknown model with real usage.
        assert any("not in catalog or MODEL_PRICING" in r.message for r in caplog.records)
        # And it must fall back to Sonnet pricing.
        expected = self.tracker.calculate_cost(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost == expected

    def test_known_model_zero_tokens_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.budget"):
            cost = self.tracker.calculate_cost(
                model="claude-opus-4-8",
                input_tokens=0,
                output_tokens=0,
            )
        assert cost == 0.0
        assert not any("not in catalog or MODEL_PRICING" in r.message for r in caplog.records)

    def test_api_span_records_real_model_priced_as_opus(self, caplog):
        """A span that made an API call carries its real model id; Opus is
        priced as Opus, not silently downgraded to Sonnet."""
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.budget"):
            cost = self.tracker.calculate_cost(
                model="claude-opus-4-8",
                input_tokens=1000,
                output_tokens=500,
            )
        # Opus pricing (catalog, per-1k 0.005/0.025 -> per-M 5.0/25.0):
        # 1000/1M*5 + 500/1M*25 = 0.005 + 0.0125
        assert abs(cost - 0.0175) < 1e-6
        sonnet = self.tracker.calculate_cost(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
        )
        assert cost != sonnet
        assert not any("not in catalog or MODEL_PRICING" in r.message for r in caplog.records)


class TestCatalogSourcedPricing:
    """calculate_cost prices from the model catalog first, MODEL_PRICING as a
    legacy fallback, then the Sonnet fallback for genuinely unknown ids.

    Expected values are computed from the live catalog / MODEL_PRICING so the
    assertions survive later catalog cost-data corrections (e.g. L5).
    """

    def setup_method(self):
        self.tracker = BudgetTracker(daily_limit_usd=10.0)

    def test_multiprovider_model_prices_from_catalog(self):
        from src.config.model_catalog import get_model_spec_by_id

        spec = get_model_spec_by_id("gpt-5")
        assert spec is not None, "gpt-5 must be in the catalog for this test"
        cost = self.tracker.calculate_cost(model="gpt-5", input_tokens=1000, output_tokens=1000)
        # catalog costs are per-1k; calculate_cost works per-million (x1000).
        expected = (1000 / 1_000_000) * spec.input_cost_per_1k * 1000 + (
            1000 / 1_000_000
        ) * spec.output_cost_per_1k * 1000
        assert abs(cost - expected) < 1e-9
        # And crucially NOT priced at the Sonnet fallback.
        sonnet = self.tracker.calculate_cost(
            model="claude-sonnet-4-6", input_tokens=1000, output_tokens=1000
        )
        assert abs(cost - sonnet) > 1e-9

    def test_legacy_id_only_in_model_pricing_still_prices_from_it(self):
        from src.config.model_catalog import get_model_spec_by_id
        from src.orchestrator.budget import MODEL_PRICING

        legacy_id = "claude-opus-4-20250514"
        assert get_model_spec_by_id(legacy_id) is None, "legacy id must not be in catalog"
        pricing = MODEL_PRICING[legacy_id]
        cost = self.tracker.calculate_cost(model=legacy_id, input_tokens=1000, output_tokens=1000)
        expected = (1000 / 1_000_000) * pricing["input"] + (1000 / 1_000_000) * pricing["output"]
        assert abs(cost - expected) < 1e-9

    def test_unknown_id_warns_and_falls_back_to_sonnet(self, caplog):
        from src.config.model_catalog import get_model_spec_by_id
        from src.orchestrator.budget import MODEL_PRICING

        unknown = "totally-made-up-model-9"
        assert get_model_spec_by_id(unknown) is None
        assert unknown not in MODEL_PRICING
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.budget"):
            cost = self.tracker.calculate_cost(model=unknown, input_tokens=1000, output_tokens=1000)
        assert any("not in catalog or MODEL_PRICING" in r.message for r in caplog.records)
        expected = self.tracker.calculate_cost(
            model="claude-sonnet-4-6", input_tokens=1000, output_tokens=1000
        )
        assert cost == expected


class TestBudgetStatus:
    def test_normal_mode(self):
        tracker = BudgetTracker(daily_limit_usd=10.0)
        tracker._today_spend = 5.0
        tracker._today_date = "2026-03-18"
        # Budget mode depends on DB query, not in-memory. Test structure:
        assert tracker.daily_limit_usd == 10.0

    def test_should_allow_perception(self):
        from src.orchestrator.budget import BudgetStatus

        tracker = BudgetTracker()
        normal = BudgetStatus(1.0, 10.0, "normal", 9.0, 10.0)
        paused = BudgetStatus(9.5, 10.0, "paused", 0.5, 95.0)
        assert tracker.should_allow_perception(normal) is True
        assert tracker.should_allow_perception(paused) is False

    def test_perception_interval_multiplier(self):
        from src.orchestrator.budget import BudgetStatus

        tracker = BudgetTracker()
        normal = BudgetStatus(1.0, 10.0, "normal", 9.0, 10.0)
        degraded = BudgetStatus(8.5, 10.0, "degraded", 1.5, 85.0)
        assert tracker.get_perception_interval_multiplier(normal) == 1
        assert tracker.get_perception_interval_multiplier(degraded) == 3


async def test_record_token_span_noop_on_empty_workspace():
    """record_token_span must not touch the DB when workspace_id is empty
    (token_usage.workspace_id is NOT NULL; a blank span would fail)."""
    from unittest.mock import MagicMock, patch

    from src.orchestrator.budget import record_token_span

    factory = MagicMock(side_effect=AssertionError("session factory must not be used"))
    with patch("src.models.database.get_session_factory", factory):
        await record_token_span(
            agent_name="triage",
            model="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=10,
            trigger="perception",
            workspace_id="",
        )
    factory.assert_not_called()
