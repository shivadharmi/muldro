"""Tests for ServerTrustRecord and CapabilityBinding models."""

from src.models.capability_binding import CapabilityBinding
from src.models.ids import generate_id, validate_typed_id
from src.models.server_trust import ServerTrustRecord


class TestServerTrustRecord:
    def test_generate_trust_id(self):
        trust_id = generate_id("trs")
        assert trust_id.startswith("trs_")
        assert validate_typed_id(trust_id, "trs")

    def test_create_trust_record(self):
        record = ServerTrustRecord(
            trust_id=generate_id("trs"),
            workspace_id="ws_test",
            server_name="github",
            trust_tier="T1",
            verified_by="anthropic",
            status="active",
        )
        assert record.server_name == "github"
        assert record.trust_tier == "T1"
        assert record.status == "active"

    def test_trust_tier_values(self):
        for tier in ["T0", "T1", "T2", "T3"]:
            record = ServerTrustRecord(
                trust_id=generate_id("trs"),
                workspace_id="ws_test",
                server_name=f"server-{tier}",
                trust_tier=tier,
                status="active",
            )
            assert record.trust_tier == tier


class TestCapabilityBinding:
    def test_generate_binding_id(self):
        binding_id = generate_id("capb")
        assert binding_id.startswith("capb_")
        assert validate_typed_id(binding_id, "capb")

    def test_create_binding(self):
        binding = CapabilityBinding(
            binding_id=generate_id("capb"),
            workspace_id="ws_test",
            capability="email.send",
            family="email",
            backend_type="native",
            backend_ref="google-workspace",
            tool_name="gmail_send",
            priority=10,
            enabled=True,
        )
        assert binding.capability == "email.send"
        assert binding.family == "email"
        assert binding.backend_type == "native"
        assert binding.priority == 10

    def test_backend_types(self):
        for bt in ["native", "mcp_official", "mcp_user"]:
            binding = CapabilityBinding(
                binding_id=generate_id("capb"),
                workspace_id="ws_test",
                capability="email.send",
                family="email",
                backend_type=bt,
                backend_ref="ref",
                tool_name="tool",
            )
            assert binding.backend_type == bt
