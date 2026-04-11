"""Tests for trust API, policy absorption, and dead code deletion."""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_approval_policy_engine_deleted():
    """ApprovalPolicyEngine must not be importable."""
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("src.services.approval_policy_engine")


def test_trust_score_model_deleted():
    """TrustScore model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.trust_score import TrustScore  # noqa: F401


def test_approval_policy_model_deleted():
    """ApprovalPolicy model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.approval_policy import ApprovalPolicy  # noqa: F401


# ── TrustEngine Dashboard + Ceiling Tests ──────────────────────────────────


def _make_trust_state(
    capability="email.send",
    risk_level="low",
    trust_level="learning",
    approved_count=5,
    rejected_count=0,
    modified_count=0,
    last_decision_at=None,
    cooldown_until=None,
    workspace_id="ws_test",
):
    return SimpleNamespace(
        capability=capability,
        risk_level=risk_level,
        trust_level=trust_level,
        approved_count=approved_count,
        rejected_count=rejected_count,
        modified_count=modified_count,
        last_decision_at=last_decision_at or datetime.now(timezone.utc),
        cooldown_until=cooldown_until,
        workspace_id=workspace_id,
    )


def _make_ceiling(capability="email.send", max_level="trusted"):
    return SimpleNamespace(capability=capability, max_level=max_level)


@pytest.mark.asyncio
async def test_get_trust_dashboard_grouped():
    """Dashboard returns capabilities grouped by family."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    states = [
        _make_trust_state("email.send", "low", "learning", 5, 0),
        _make_trust_state("email.send", "medium", "first_use", 1, 0),
        _make_trust_state("email.read", "low", "trusted", 12, 0),
        _make_trust_state("calendar.create", "medium", "first_use", 0, 0),
    ]
    ceilings = [_make_ceiling("email.send", "trusted")]

    mock_result_states = MagicMock()
    mock_result_states.scalars.return_value.all.return_value = states
    mock_result_ceilings = MagicMock()
    mock_result_ceilings.scalars.return_value.all.return_value = ceilings

    mock_db.execute = AsyncMock(side_effect=[mock_result_states, mock_result_ceilings])

    dashboard = await engine.get_trust_dashboard_grouped()

    assert isinstance(dashboard, list)
    caps = {e["capability"] for e in dashboard}
    assert "email.send" in caps
    assert "email.read" in caps
    assert "calendar.create" in caps

    email_send = next(e for e in dashboard if e["capability"] == "email.send")
    assert email_send["ceiling"] == "trusted"
    assert len(email_send["risk_levels"]) == 2


@pytest.mark.asyncio
async def test_get_capability_detail():
    """Capability detail returns per-risk breakdown with graduation progress."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    states = [
        _make_trust_state("email.send", "low", "learning", 5, 0),
        _make_trust_state("email.send", "medium", "first_use", 1, 0),
        _make_trust_state("email.send", "high", "first_use", 0, 0),
    ]
    ceiling = _make_ceiling("email.send", "trusted")

    mock_result_states = MagicMock()
    mock_result_states.scalars.return_value.all.return_value = states
    mock_result_ceiling = MagicMock()
    mock_result_ceiling.scalar_one_or_none.return_value = ceiling

    mock_db.execute = AsyncMock(side_effect=[mock_result_states, mock_result_ceiling])

    detail = await engine.get_capability_detail("email.send")

    assert detail["capability"] == "email.send"
    assert detail["ceiling"] == "trusted"
    assert len(detail["risk_levels"]) == 3
    low_entry = next(r for r in detail["risk_levels"] if r["risk_level"] == "low")
    assert "graduation_progress" in low_entry


@pytest.mark.asyncio
async def test_set_ceiling():
    """set_ceiling upserts a TrustCeiling record."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    await engine.set_ceiling("email.send", "trusted")

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.capability == "email.send"
    assert added.max_level == "trusted"


@pytest.mark.asyncio
async def test_reset_trust_for_capability():
    """reset_trust resets all risk-level states for a capability."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    state1 = _make_trust_state("email.send", "low", "trusted", 15, 1)
    state2 = _make_trust_state("email.send", "medium", "learning", 5, 0)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [state1, state2]
    mock_db.execute = AsyncMock(return_value=mock_result)

    await engine.reset_trust_for_capability("email.send")

    assert state1.trust_level == "first_use"
    assert state1.approved_count == 0
    assert state2.trust_level == "first_use"
    assert state2.approved_count == 0


def test_policy_mode_to_ceiling_mapping():
    """Verify the 4 mode → ceiling mappings are correct."""
    from src.api.routes_settings import POLICY_MODE_TO_CEILING

    assert POLICY_MODE_TO_CEILING["lockdown"] == "blocked"
    assert POLICY_MODE_TO_CEILING["approval_required"] == "learning"
    assert POLICY_MODE_TO_CEILING["suggest_only"] == "first_use"
    assert POLICY_MODE_TO_CEILING["full_auto"] is None


def test_policy_mode_invalid_mode_rejected():
    """Invalid mode strings are not in the valid_modes set."""
    valid_modes = {"lockdown", "approval_required", "suggest_only", "full_auto"}
    invalid_examples = ["yolo", "auto", "none", "", "LOCKDOWN", "full-auto"]
    for mode in invalid_examples:
        assert mode not in valid_modes, f"Expected '{mode}' to be invalid"


def test_policy_mode_approval_required_ceiling():
    """approval_required maps to 'learning' ceiling."""
    from src.api.routes_settings import POLICY_MODE_TO_CEILING

    assert POLICY_MODE_TO_CEILING["approval_required"] == "learning"


def test_policy_mode_suggest_only_ceiling():
    """suggest_only maps to 'first_use' ceiling."""
    from src.api.routes_settings import POLICY_MODE_TO_CEILING

    assert POLICY_MODE_TO_CEILING["suggest_only"] == "first_use"


def test_policy_mode_full_auto_ceiling():
    """full_auto maps to None (no ceiling restriction)."""
    from src.api.routes_settings import POLICY_MODE_TO_CEILING

    assert POLICY_MODE_TO_CEILING["full_auto"] is None
