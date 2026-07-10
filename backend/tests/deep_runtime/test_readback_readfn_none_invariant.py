"""Step 10A A2: lock the ``read_fn=None`` -> never-CONTRADICTED invariant of ReadBackVerifier.

``ReadBackVerifier.verify_step`` (readback.py:43-84) short-circuits to UNVERIFIED at lines
63-64 whenever ``self._read_fn is None`` — BEFORE the read-back call that alone can produce
CONTRADICTED (line 84, only reachable after a successful ``read_fn`` invocation). So with
``read_fn=None`` an irreversible write can NEVER be false-CONTRADICTED. This is the guard
that keeps the mock-only ``calendar.create`` post-condition (see
``tests/verification/test_readback.py::test_post_condition_contradicted_when_readback_absent``,
which uses a REAL ``read_fn`` mock to get CONTRADICTED) from false-firing in production, where
``read_fn=None`` is wired until a real per-connector read seam lands (B4).

TEST-ONLY guard-lock — no change to ``readback.py``. Regression, not RED->GREEN implement.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from src.services.verification.post_conditions import POST_CONDITIONS
from src.services.verification.readback import ReadBackVerifier, VerifyVerdict


def _irreversible_risk():
    return SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="high")


async def test_readfn_none_on_calendar_create_is_unverified_never_contradicted():
    """calendar.create CAN return CONTRADICTED (see
    test_post_condition_contradicted_when_readback_absent); with read_fn=None it MUST
    short-circuit to UNVERIFIED, never CONTRADICTED (the footgun the real read_fn
    defers to B4)."""
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_irreversible_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED
    assert verdict != VerifyVerdict.CONTRADICTED


@pytest.mark.parametrize("capability", sorted(POST_CONDITIONS.keys()))
async def test_readfn_none_never_contradicts_any_registered_postcondition_cap(capability):
    """Universal invariant: with read_fn=None, NO registered post-condition capability can be
    CONTRADICTED — CONTRADICTED requires a successful read_fn call (readback.py:84), unreachable
    when read_fn is None. Robust to newly-added post-condition caps."""
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability=capability,
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_irreversible_risk(),
    )
    assert verdict != VerifyVerdict.CONTRADICTED


async def test_readback_middleware_wired_read_fn_none_preserves_invariant():
    """The deep readback MIDDLEWARE, built as production wires it (read_fn=None,
    agent_invoker.py:399), preserves the invariant end-to-end: an irreversible write
    (calendar.create) is annotated UNVERIFIED, never CONTRADICTED, and carries NO
    escalation payload."""
    from src.deep_runtime.middleware.readback import make_readback_middleware

    mw = make_readback_middleware(
        workspace_id="ws",
        authorization_source="autonomous",
        resolve_capability=AsyncMock(return_value="calendar.create"),
        assess_risk=AsyncMock(return_value=_irreversible_risk()),
        read_fn=None,
        record_confirmed_outcome=AsyncMock(),
    )

    async def handler(request):
        return ToolMessage(
            content=json.dumps({"event_id": "evt_1"}),
            tool_call_id="tc1",
            name="create_event",
            status="success",
        )

    request = SimpleNamespace(
        tool_call={"name": "create_event", "args": {"calendar_id": "cal_1"}, "id": "tc1"}
    )
    result = await mw.awrap_tool_call(request, handler)
    verification = json.loads(result.content)["verification"]
    assert verification["verdict"] == VerifyVerdict.UNVERIFIED.value
    assert verification["verdict"] != VerifyVerdict.CONTRADICTED.value
    assert "escalation" not in verification  # CONTRADICTED would add an escalation payload
