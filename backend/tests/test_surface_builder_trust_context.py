"""SurfaceService attaches the computed trust_context to awaiting-approval run
surfaces on the REST path (previously computed then discarded)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.services.surface_builder import SurfaceService


def test_run_trust_context_returns_computed_dict():
    svc = SurfaceService(db=MagicMock(), workspace_id="ws_1")
    approval = MagicMock(artifact_refs={"tool_name": "email.send"}, risk_level="low")
    svc._latest_pending_approval = AsyncMock(return_value=approval)
    svc._get_trust_context = AsyncMock(
        return_value={"trust_level": "learning", "label": "Similar to 4 approvals"}
    )

    result = asyncio.run(svc._run_trust_context("run_01"))
    assert result == {"trust_level": "learning", "label": "Similar to 4 approvals"}


def test_run_trust_context_returns_none_without_pending_approval():
    svc = SurfaceService(db=MagicMock(), workspace_id="ws_1")
    svc._latest_pending_approval = AsyncMock(return_value=None)

    result = asyncio.run(svc._run_trust_context("run_01"))
    assert result is None
