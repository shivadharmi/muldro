"""Tests for ApprovalPolicy model and ToolPolicy capability classification."""

from src.models.approval_policy import ApprovalPolicy
from src.models.ids import generate_id, validate_typed_id
from src.orchestrator.tool_policy import ToolPolicy


class TestApprovalPolicyModel:
    def test_generate_policy_id(self):
        policy_id = generate_id("apol")
        assert policy_id.startswith("apol_")
        assert validate_typed_id(policy_id, "apol")

    def test_create_policy(self):
        policy = ApprovalPolicy(
            policy_id=generate_id("apol"),
            workspace_id="ws_test",
            capability_pattern="email.*",
            approval_mode="always",
            risk_threshold="high",
            enabled=True,
        )
        assert policy.capability_pattern == "email.*"
        assert policy.approval_mode == "always"
        assert policy.enabled is True

    def test_trust_tier_min(self):
        policy = ApprovalPolicy(
            policy_id=generate_id("apol"),
            workspace_id="ws_test",
            capability_pattern="messaging.send",
            trust_tier_min="T1",
            approval_mode="high_risk_only",
        )
        assert policy.trust_tier_min == "T1"


class TestToolPolicyCapabilityClassification:
    def test_read_only_tool_is_allowed(self):
        policy = ToolPolicy()
        result = policy._classify_via_capability("gmail_list", None)
        assert result is not None
        assert not result.is_blocked
        assert not result.is_write
        assert result.risk_level == "low"

    def test_write_tool_requires_approval(self):
        policy = ToolPolicy()
        result = policy._classify_via_capability("gmail_send", None)
        assert result is not None
        assert not result.is_blocked
        assert result.is_write
        assert result.risk_level == "high"

    def test_t3_trust_escalates_risk(self):
        policy = ToolPolicy()
        # Even a medium-risk tool should require approval at T3
        result = policy._classify_via_capability("calendar_create_event", "T3")
        assert result is not None
        assert result.is_write
        # Risk should be escalated
        assert result.risk_level in ("medium", "high")

    def test_t0_internal_tools(self):
        policy = ToolPolicy()
        result = policy._classify_via_capability("search_memory", None)
        assert result is not None
        assert not result.is_write
        assert result.risk_level == "low"

    def test_unknown_tool_returns_none(self):
        policy = ToolPolicy()
        result = policy._classify_via_capability("totally_unknown_tool", None)
        assert result is None

    def test_critical_risk_tool_blocked(self):
        policy = ToolPolicy()
        result = policy._classify_via_capability("gmail_delete", None)
        assert result is not None
        assert result.is_blocked
        assert result.risk_level == "critical"
