"""B12 / P3.2 — persisted-REST approval → rich ``ApprovalContext`` unification.

Every persisted approval surface historically dropped the rich context. This makes
them all carry the SAME rich ``src.contracts.ApprovalContext`` the live-WS path emits,
sourced from ``UISurface.payload.last_surface_update.approval``:
  * the history LIST endpoint (``resolve_history_approval``);
  * the run/summary approval DETAIL tab (``build_run_approval_tab``).

Single shared rule (``extract_persisted_rich_approval``):
  * persisted approval present + well-formed → RICH ``ApprovalContext``;
  * absent → ABSENT (each caller applies its own byte-neutral fallback);
  * present but MALFORMED (missing a required field / not a dict) → MALFORMED
    (each caller FAILS CLOSED, never a half-rendered card).
"""

from unittest.mock import AsyncMock, MagicMock

from src.api.schemas_history import HistoryApprovalContext, HistoryItemResponse
from src.contracts import ApprovalContext


def _rich_approval_dict(**overrides) -> dict:
    """The approval sub-dict as ``emit_surface_update`` persists it under
    ``last_surface_update.approval`` (an ``ApprovalContext.model_dump(mode="json")``)."""
    base = {
        "approval_id": "apr_1",
        "step_description": "Send the launch email",
        "risk_level": "high",
        "trust_level": "learning",
        "expires_at": None,
        "triggering_step_id": "step_2",
        "graduation_hint": "3 more approvals to auto-run",
        "risk_reasoning": "Sends an external email to investors",
        "trust_context": "Similar to 2 prior approvals",
        "reversible": False,
        "blast_radius": "external",
        "effective_trust_level": "learning",
        "approved_count": 2,
        "rejected_count": 0,
    }
    base.update(overrides)
    return base


def _surface_payload(approval: object) -> dict:
    """A persisted run ``UISurface.payload`` carrying the last surface update."""
    return {
        "metadata": {"run_id": "run_1"},
        "last_surface_update": {
            "surface_id": "run_1",
            "phase": "approval_needed",
            "approval": approval,
        },
    }


def _thin() -> HistoryApprovalContext:
    return HistoryApprovalContext(
        approval_id="apr_1",
        step_id="step_2",
        step_description="Send the launch email",
        risk_level="high",
    )


# ── (b) enrichment: persisted rich context → rich ApprovalContext returned ──────
def test_resolve_returns_rich_when_persisted_wellformed():
    from src.api.routes_history import resolve_history_approval

    out = resolve_history_approval(_thin(), _surface_payload(_rich_approval_dict()))

    assert isinstance(out, ApprovalContext)
    # Evidence fields the thin shape could never carry:
    assert out.risk_reasoning == "Sends an external email to investors"
    assert out.blast_radius == "external"
    assert out.approved_count == 2
    assert out.graduation_hint == "3 more approvals to auto-run"
    assert out.reversible is False


# ── fallback: no persisted rich context → thin passthrough (byte-neutral) ───────
def test_resolve_falls_back_to_thin_when_absent():
    from src.api.routes_history import resolve_history_approval

    thin = _thin()
    # No surface at all.
    assert resolve_history_approval(thin, None) is thin
    # Surface exists but the approval key is null.
    assert resolve_history_approval(thin, _surface_payload(None)) is thin
    # Surface exists but carries no last_surface_update.
    assert resolve_history_approval(thin, {"metadata": {"run_id": "run_1"}}) is thin


# ── (a) fail closed: persisted context present but malformed → None ─────────────
def test_resolve_fails_closed_when_malformed():
    from src.api.routes_history import resolve_history_approval

    # A rich payload missing a REQUIRED ApprovalContext field (risk_reasoning).
    malformed = _rich_approval_dict()
    del malformed["risk_reasoning"]
    assert resolve_history_approval(_thin(), _surface_payload(malformed)) is None

    # A non-dict approval payload is also malformed → fail closed.
    assert resolve_history_approval(_thin(), _surface_payload("not-a-dict")) is None


# ── byte-neutrality + no union coercion in the response model ───────────────────
def test_history_item_serializes_thin_byte_neutral_and_rich_full():
    thin_item = HistoryItemResponse(
        run_id="run_1",
        status="awaiting_approval",
        step_count=0,
        completed_step_count=0,
        approval=_thin(),
    )
    thin_dump = thin_item.model_dump(mode="json")["approval"]
    # EXACT thin shape — no rich fields leak in (proves no upward union coercion).
    assert thin_dump == {
        "approval_id": "apr_1",
        "step_id": "step_2",
        "step_description": "Send the launch email",
        "risk_level": "high",
        "trust_level": None,
    }

    rich_item = HistoryItemResponse(
        run_id="run_1",
        status="awaiting_approval",
        step_count=0,
        completed_step_count=0,
        approval=ApprovalContext.model_validate(_rich_approval_dict()),
    )
    rich_dump = rich_item.model_dump(mode="json")["approval"]
    # Rich evidence survives serialization (proves no downward coercion to thin).
    assert rich_dump["risk_reasoning"] == "Sends an external email to investors"
    assert rich_dump["blast_radius"] == "external"
    assert rich_dump["approved_count"] == 2


# ── Shared classifier: one source of the absent / rich / malformed rule ─────────
def test_extract_persisted_rich_approval_classifies():
    from src.services.approval_resolution import (
        PersistedApprovalStatus,
        extract_persisted_rich_approval,
    )

    # ABSENT — caller applies its own byte-neutral fallback.
    assert extract_persisted_rich_approval(None)[0] is PersistedApprovalStatus.ABSENT
    assert extract_persisted_rich_approval(_surface_payload(None))[0] is (
        PersistedApprovalStatus.ABSENT
    )
    assert extract_persisted_rich_approval({"metadata": {}})[0] is PersistedApprovalStatus.ABSENT

    # RICH — well-formed ApprovalContext.
    status, ctx = extract_persisted_rich_approval(_surface_payload(_rich_approval_dict()))
    assert status is PersistedApprovalStatus.RICH
    assert isinstance(ctx, ApprovalContext)
    assert ctx.graduation_hint == "3 more approvals to auto-run"

    # MALFORMED — present but missing a required field / not a dict.
    bad = _rich_approval_dict()
    del bad["risk_reasoning"]
    assert extract_persisted_rich_approval(_surface_payload(bad))[0] is (
        PersistedApprovalStatus.MALFORMED
    )
    assert extract_persisted_rich_approval(_surface_payload("not-a-dict"))[0] is (
        PersistedApprovalStatus.MALFORMED
    )


# ── Run/summary approval DETAIL tab enrichment (build_run_approval_tab) ──────────
def _mock_approval(
    approval_id: str = "apr_1",
    title: str = "Send the launch email",
    summary: str = "Sends an external email to investors",
    risk_level: str = "high",
    artifact_refs: dict | None = None,
) -> MagicMock:
    apr = MagicMock()
    apr.approval_id = approval_id
    apr.title = title
    apr.summary = summary
    apr.risk_level = risk_level
    apr.expires_at = None
    apr.artifact_refs = artifact_refs if artifact_refs is not None else {}
    apr.status = "pending"
    return apr


def _mock_db_with_approvals(approvals: list) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = approvals
    db.execute = AsyncMock(return_value=result)
    return db


def _run_surface(payload: dict) -> MagicMock:
    s = MagicMock()
    s.surface_id = "run_1"
    s.surface_type = "run"
    s.payload = payload
    s.workspace_id = "ws_test"
    return s


async def test_run_approval_tab_renders_rich_when_persisted_wellformed():
    from src.services.surface_detail_builders import build_run_approval_tab

    surface = _run_surface(_surface_payload(_rich_approval_dict()))
    db = _mock_db_with_approvals([_mock_approval()])

    resp = await build_run_approval_tab(db, surface)
    blob = resp.model_dump_json()

    # Rich-only evidence the thin Approval-row card could never carry:
    assert "GRADUATION" in blob
    assert "3 more approvals to auto-run" in blob  # graduation_hint
    assert "Similar to 2 prior approvals" in blob  # trust_context
    assert "trust: learning" in blob  # trust_level badge


async def test_run_approval_tab_byte_neutral_thin_when_absent():
    from src.services.surface_detail_builders import build_run_approval_tab

    surface = _run_surface({"metadata": {"run_id": "run_1"}})  # no last_surface_update
    db = _mock_db_with_approvals([_mock_approval()])

    resp = await build_run_approval_tab(db, surface)
    blob = resp.model_dump_json()

    # Today's thin card is rendered verbatim (byte-neutral) …
    assert "Send the launch email" in blob
    assert "Sends an external email to investors" in blob
    # … and NONE of the rich-only markers appear.
    assert "GRADUATION" not in blob
    assert "TRUST CONTEXT" not in blob


async def test_run_approval_tab_fails_closed_to_thin_when_malformed():
    from src.services.surface_detail_builders import build_run_approval_tab

    malformed = _rich_approval_dict()
    del malformed["risk_reasoning"]  # drop a REQUIRED ApprovalContext field
    surface = _run_surface(_surface_payload(malformed))
    db = _mock_db_with_approvals([_mock_approval()])

    resp = await build_run_approval_tab(db, surface)  # must NOT raise
    blob = resp.model_dump_json()

    # Fail closed to the COMPLETE thin card — never a half-rendered rich card.
    assert "Send the launch email" in blob
    assert "GRADUATION" not in blob
    assert "TRUST CONTEXT" not in blob
