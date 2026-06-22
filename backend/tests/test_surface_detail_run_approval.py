"""Tests for the run Approval detail tab + conditional approval tab wiring.

Phase 1 of the A2UI approval remediation: an ``awaiting_approval`` run must be
actionable from its persisted detail modal. The run surface gains an Approval
tab (rendered via ``units.approval_card``) and ``build_detail_config`` appends
that tab + defaults to it only when the run is awaiting approval.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.approvals import Approval
from src.services.surface_detail_builders import TAB_BUILDERS
from src.services.surface_detail_builders.run import build_run_approval_tab
from src.ui.contracts import DetailTabResponse
from src.ui.renderer import build_detail_config


def _mock_run_surface(surface_id: str = "run_abc123"):
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = "run"
    s.payload = {}
    s.workspace_id = "ws_test"
    return s


def _mock_db_with_approvals(approvals: list[Approval]) -> AsyncMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = approvals
    result.scalars.return_value = scalars
    db = AsyncMock()
    db.execute.return_value = result
    return db


def _build_approval() -> Approval:
    apr = Approval()
    apr.approval_id = "apr_test1"
    apr.run_id = "run_abc123"  # post-4893e16: run surface_id IS the run_id
    apr.workspace_id = "ws_test"
    apr.status = "pending"
    apr.title = "Approve step: Send launch email"
    apr.summary = "Sending an external email is irreversible."
    apr.risk_level = "high"
    apr.expires_at = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    apr.artifact_refs = {
        "capability": "email.send",
        "step_name": "Send launch email",
        "reversible": False,
        "blast_radius": "workspace",
    }
    return apr


# --- registry ------------------------------------------------------------


def test_approval_tab_registered_for_run_and_summary():
    assert TAB_BUILDERS[("run", "approval")] is build_run_approval_tab
    assert TAB_BUILDERS[("summary", "approval")] is build_run_approval_tab


# --- tab builder ---------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_tab_renders_card_when_pending():
    db = _mock_db_with_approvals([_build_approval()])

    result = await build_run_approval_tab(db, _mock_run_surface())

    assert isinstance(result, DetailTabResponse)
    assert result.tab_id == "approval"
    rendered = str(result.model_dump())
    # The actionable approval card with the locked action payload must be present.
    assert "approval.approve" in rendered
    assert "approval.reject" in rendered
    assert "apr_test1" in rendered
    # Preview context from artifact_refs.
    assert "irreversible" in rendered
    assert "workspace" in rendered


@pytest.mark.asyncio
async def test_approval_tab_empty_when_no_pending():
    db = _mock_db_with_approvals([])

    result = await build_run_approval_tab(db, _mock_run_surface())

    assert isinstance(result, DetailTabResponse)
    assert result.tab_id == "approval"
    rendered = str(result.model_dump())
    assert "No pending approval for this run." in rendered
    assert "approval.approve" not in rendered


@pytest.mark.asyncio
async def test_approval_tab_resolves_summary_prefix():
    """Summary surfaces (summary_{run_id}) resolve the run id like the siblings.

    The realistic id is ``summary_run_<ULID>`` (summary OF a run surface), so
    stripping only the outer ``summary_`` recovers the ``run_<ULID>`` run id.
    """
    db = _mock_db_with_approvals([_build_approval()])

    result = await build_run_approval_tab(db, _mock_run_surface("summary_run_abc123"))

    assert result.tab_id == "approval"
    assert "approval.approve" in str(result.model_dump())


# --- detail_config wiring ------------------------------------------------


def test_detail_config_excludes_approval_tab_by_default():
    cfg = build_detail_config("run", "run_abc")
    assert cfg is not None
    tab_ids = [t.id for t in cfg.tabs]
    assert "approval" not in tab_ids
    assert cfg.default_tab is None


def test_detail_config_includes_approval_tab_when_awaiting():
    cfg = build_detail_config(
        "run",
        "run_abc",
        extra_tabs=[("approval", "Approval")],
        default_tab="approval",
    )
    assert cfg is not None
    tab_ids = [t.id for t in cfg.tabs]
    assert "approval" in tab_ids
    assert cfg.default_tab == "approval"
    # Endpoint must point at the run's detail approval route.
    approval_tab = next(t for t in cfg.tabs if t.id == "approval")
    assert approval_tab.endpoint == "/v1/surfaces/run_abc/detail/approval"


def test_detail_config_no_duplicate_extra_tab():
    """An extra_tab that already exists is not appended twice."""
    cfg = build_detail_config("run", "run_abc", extra_tabs=[("steps", "Steps")])
    assert cfg is not None
    assert [t.id for t in cfg.tabs].count("steps") == 1
