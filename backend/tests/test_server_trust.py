"""Tests for ServerTrustRecord model."""

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
