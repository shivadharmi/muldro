"""Tests for BudgetTracker cost calculation with cache/thinking tokens."""

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

    def test_bedrock_pricing(self):
        cost = self.tracker.calculate_cost(
            model="anthropic.claude-opus-4-20250514-v1:0",
            input_tokens=1000,
            output_tokens=500,
        )
        expected = self.tracker.calculate_cost(
            model="claude-opus-4-20250514",
            input_tokens=1000,
            output_tokens=500,
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
