"""The execute_action handler enforces capabilities before doing any work.

Complements the pure-function tests in ``test_enforcement_capabilities.py``:
here we prove the gate is actually wired into ``handle_execute_action`` and
that it fails fast — before the connection is resolved (no DB) and before
OpenConnector is called. ``db=None`` is deliberate: if the handler reached
connection resolution it would blow up on ``None``, so a clean
``CapabilityDenied`` proves the check short-circuits first.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.adapter.enforcement import CapabilityDenied
from src.adapter.server import handle_execute_action
from src.orchestrator.platform_jwt import mint_platform_jwt


async def test_read_scoped_token_cannot_send():
    token = mint_platform_jwt(
        principal_id="usr_x",
        tenant_id="ws_x",
        workspace_id="ws_x",
        capabilities=["email.search"],
    )
    with patch(
        "src.adapter.server.call_openconnector",
        new_callable=AsyncMock,
    ) as mock_call:
        with pytest.raises(CapabilityDenied):
            await handle_execute_action(
                None,
                token=token,
                args={"actionId": "gmail.send", "input": {"to": "a@b.c"}},
            )
    mock_call.assert_not_awaited()
