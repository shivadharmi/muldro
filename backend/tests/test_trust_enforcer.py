"""Tests for trust enforcer."""


class TestTrustEnforcer:
    def test_t0_always_allowed(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("internal-server", "T0", "any_tool", is_write=True)
        assert result.allowed is True
        assert result.requires_approval is False

    def test_t1_read_allowed(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("official-server", "T1", "search_docs", is_write=False)
        assert result.allowed is True
        assert result.requires_approval is False

    def test_t2_write_requires_approval(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("org-server", "T2", "create_issue", is_write=True)
        assert result.allowed is True
        assert result.requires_approval is True

    def test_t3_write_requires_approval(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("user-server", "T3", "send_email", is_write=True)
        assert result.allowed is True
        assert result.requires_approval is True

    def test_t3_read_allowed(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("user-server", "T3", "list_items", is_write=False)
        assert result.allowed is True
        assert result.requires_approval is False

    def test_rate_limit_blocks_after_limit(self):
        from src.integrations.trust_enforcer import TIER_RATE_LIMITS, TrustEnforcer

        enforcer = TrustEnforcer()
        limit = TIER_RATE_LIMITS["T3"]

        for _ in range(limit):
            enforcer.record_call("user-server")

        result = enforcer.check("user-server", "T3", "search", is_write=False)
        assert result.allowed is False
        assert "Rate limit" in result.reason

    def test_concurrent_limit_blocks(self):
        from src.integrations.trust_enforcer import TIER_MAX_CONCURRENT, TrustEnforcer

        enforcer = TrustEnforcer()
        limit = TIER_MAX_CONCURRENT["T3"]

        for _ in range(limit):
            enforcer.record_call("user-server")

        result = enforcer.check("user-server", "T3", "search", is_write=False)
        assert result.allowed is False

    def test_complete_call_reduces_concurrent(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        enforcer.record_call("user-server")
        enforcer.complete_call("user-server")

        usage = enforcer.get_usage("user-server")
        assert usage["active_calls"] == 0

    def test_rate_limit_remaining(self):
        from src.integrations.trust_enforcer import TrustEnforcer

        enforcer = TrustEnforcer()
        result = enforcer.check("org-server", "T2", "search", is_write=False)
        assert result.rate_limit_remaining is not None
        assert result.rate_limit_remaining > 0
