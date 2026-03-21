"""Tests for approval impact service."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_WORKSPACE_ID


class TestApprovalImpact:
    async def test_reversibility_assessment_email(self):
        from src.services.approval_impact import _assess_reversibility

        rev, detail = _assess_reversibility("send_email")
        assert rev == "irreversible"
        assert "cannot be undone" in detail

    async def test_reversibility_assessment_issue(self):
        from src.services.approval_impact import _assess_reversibility

        rev, detail = _assess_reversibility("create_issue")
        assert rev == "partially_reversible"

    async def test_reversibility_assessment_unknown(self):
        from src.services.approval_impact import _assess_reversibility

        rev, detail = _assess_reversibility("update_config")
        assert rev == "reversible"

    async def test_policy_explanation_critical(self):
        from src.services.approval_impact import _explain_policy

        explanation = _explain_policy("delete_data", "critical")
        assert "critical risk" in explanation

    async def test_policy_explanation_high(self):
        from src.services.approval_impact import _explain_policy

        explanation = _explain_policy("send_email", "high")
        assert "high risk" in explanation.lower()

    async def test_get_impact_not_found(self):
        from src.services.approval_impact import ApprovalImpactService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = ApprovalImpactService(db, TEST_WORKSPACE_ID)
        impact = await svc.get_impact("apr_nonexistent")
        assert impact.risk_level == "unknown"
        assert impact.affected_entity_count == 0


class TestApprovalImpactWithApproval:
    async def test_get_impact_with_approval(self):
        from src.services.approval_impact import ApprovalImpactService

        mock_approval = MagicMock()
        mock_approval.approval_id = "apr_001"
        mock_approval.approval_type = "send_email"
        mock_approval.risk_level = "high"
        mock_approval.execution_id = None
        mock_approval.title = "Send email to investor"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:  # approval lookup
                result.scalar_one_or_none.return_value = mock_approval
            elif call_count == 2:  # affected entities (approval lookup again)
                result.scalar_one_or_none.return_value = mock_approval
            else:  # entity search
                result.scalars.return_value.all.return_value = []
            return result

        db = AsyncMock()
        db.execute = mock_execute
        db.scalar = AsyncMock(return_value=0)

        svc = ApprovalImpactService(db, TEST_WORKSPACE_ID)
        impact = await svc.get_impact("apr_001")
        assert impact.risk_level == "high"
        assert impact.reversibility == "irreversible"
        assert len(impact.downstream_effects) > 0
        assert any("email" in e.lower() for e in impact.downstream_effects)
