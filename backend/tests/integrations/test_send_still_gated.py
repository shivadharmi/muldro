"""Guard test: the Gmail gateway seam sits BELOW the approval boundary.

`src/adapter/` (Connection Context Adapter — `enforcement.py`,
`openconnector_client.py`) routes Gmail tool calls through ToolHive/
OpenConnector. It is a pure dispatch-time transport concern: allowlist a
handful of actions, force the connection identity, strip secrets from the
response. None of that touches capability_scope, TrustEngine, or
permission_gate — those middlewares run *above* dispatch and are unchanged
by which transport a tool call is routed through.

This test does not import anything from `src/adapter/` — it guards the
*trust* side of the seam: no matter what routes traffic to Gmail below
dispatch, `email.send` (a "high"-risk, destructive capability per
CLAUDE.md's Trust Infrastructure) must still come out of
`TrustEngine.evaluate()` as `approval_required`, never an auto-execute
decision, at a fresh ("first_use") trust level — the level every
workspace/capability pair starts at, gateway or no gateway. It also pins
RiskAssessor's fail-closed contract (`assess_risk` degrades to
`risk_level="high"` on any failure) so a gateway-induced assessment failure
cannot silently downgrade risk and slip past the gate.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.risk_assessor import RiskAssessment, assess_risk
from src.services.trust_engine import TrustEngine


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _make_trust_state(trust_level: str = "first_use"):
    state = MagicMock()
    state.trust_level = trust_level
    state.approved_count = 0
    state.rejected_count = 0
    state.modified_count = 0
    state.cooldown_until = None
    return state


def _make_ceiling(max_level: str = "autonomous"):
    ceiling = MagicMock()
    ceiling.max_level = max_level
    return ceiling


class TestGmailSendStillGatedAtFirstUse:
    """A gateway-routed email.send at a fresh trust level must require approval."""

    async def test_high_risk_email_send_requires_approval_not_auto_execute(self, mock_db):
        engine = TrustEngine(mock_db, workspace_id="ws_gmail_gateway_test")
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("first_use"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("autonomous"))

        risk = RiskAssessment(
            risk_level="high",
            reasoning="Gateway-routed send — destructive, external-facing action",
            reversible=False,
            blast_radius="external_single",
        )

        decision = await engine.evaluate("email.send", risk)

        assert decision.decision == "approval_required"
        assert decision.decision not in (
            "auto_execute",
            "auto_execute_notify",
            "auto_execute_silent",
        )

    async def test_high_risk_email_send_requires_approval_even_at_autonomous_trust(self, mock_db):
        """CLAUDE.md: risk_level='high' maps to approval_required at *every* trust
        level, including 'autonomous' — the ceiling on runaway autonomy that a
        gateway seam must not be able to route around."""
        engine = TrustEngine(mock_db, workspace_id="ws_gmail_gateway_test")
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("autonomous"))

        risk = RiskAssessment(risk_level="high", reasoning="forced-high test case")

        decision = await engine.evaluate("email.send", risk)

        assert decision.decision == "approval_required"


class TestRiskAssessorFailsClosedForGmailSend:
    """A gateway-induced assessment failure must not silently downgrade risk."""

    async def test_assess_risk_failure_falls_back_to_high_not_medium(self):
        with patch(
            "src.services.risk_assessor.complete_text",
            new=AsyncMock(side_effect=RuntimeError("gateway/transport failure")),
        ):
            assessment = await assess_risk(
                capability="email.send",
                step_input={"to": "someone@example.com", "subject": "test"},
                user_context={},
            )

        assert assessment.risk_level == "high"
        assert assessment.risk_level != "medium"

    async def test_fail_closed_assessment_then_evaluates_to_approval_required(self, mock_db):
        """End-to-end guard: fail-closed RiskAssessment fed into TrustEngine still
        yields approval_required, even at a fully autonomous trust level."""
        with patch(
            "src.services.risk_assessor.complete_text",
            new=AsyncMock(side_effect=RuntimeError("gateway/transport failure")),
        ):
            assessment = await assess_risk(
                capability="email.send",
                step_input={"to": "someone@example.com"},
                user_context={},
            )

        engine = TrustEngine(mock_db, workspace_id="ws_gmail_gateway_test")
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("autonomous"))

        decision = await engine.evaluate("email.send", assessment)

        assert decision.decision == "approval_required"
