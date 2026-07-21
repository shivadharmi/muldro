"""SurfaceService attaches the computed trust_context to awaiting-approval run
surfaces on the REST path (previously computed then discarded).

``_approval_risk_and_flags`` already looks up the pending Approval and calls
``_get_trust_context`` to derive the "LEARNING"/"TRUSTED"/... flag — it now
also returns that dict as its third element so callers get risk + flags +
trust_context from a single pending-approval + trust-state round trip
instead of repeating both lookups.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.services.surface_builder import SurfaceService


def test_approval_risk_and_flags_returns_trust_context_as_third_element():
    svc = SurfaceService(db=MagicMock(), workspace_id="ws_1")
    approval = MagicMock(
        risk_level="high",
        artifact_refs={"tool_name": "email.send", "reversible": False},
    )
    svc._latest_pending_approval = AsyncMock(return_value=approval)
    svc._get_trust_context = AsyncMock(
        return_value={"trust_level": "learning", "label": "Similar to 4 approvals"}
    )

    risk_value, flags, trust_context = asyncio.run(svc._approval_risk_and_flags("run_01"))

    assert risk_value == "high"
    assert "Irreversible" in flags
    assert "LEARNING" in flags
    assert trust_context == {"trust_level": "learning", "label": "Similar to 4 approvals"}


def test_approval_risk_and_flags_returns_none_trust_context_without_pending_approval():
    svc = SurfaceService(db=MagicMock(), workspace_id="ws_1")
    svc._latest_pending_approval = AsyncMock(return_value=None)

    risk_value, flags, trust_context = asyncio.run(svc._approval_risk_and_flags("run_01"))

    assert risk_value is None
    assert flags == []
    assert trust_context is None
