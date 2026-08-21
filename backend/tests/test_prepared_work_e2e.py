"""The prepare-then-confirm arc, end to end (single-lead cutover, Task 14).

Five subsystems cooperate to turn a write nobody can confirm into finished, reviewable work:
``presence`` decides there is no human on the turn; the ``permission_gate`` turns its CONFIRM
verdict into a PREPARE; ``approval_persistence`` writes the reviewable record; the deterministic
replay in ``prepared_actions`` runs exactly that record later; and the confirmation route reads
the outcome. Each has thorough unit tests. **None of them tests the joins.**

This is the test that fails when a refactor keeps all five green while breaking the seams
between them — the failure mode that actually ships. It therefore walks ONE arc with the REAL
gate and the REAL ``execute_prepared_action``, and fakes only the four things that are not
seams: the DB session, the tool registry, the idempotency ledger, and the tool dispatcher.

The doubles are IMPORTED from the unit suites that own them rather than re-declared, so a
double that drifts from what its subsystem expects breaks here too instead of quietly
diverging into a second, kinder version of reality.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage

from src.api.routes_approvals import _guard_not_chat_approval
from src.services.prepared_actions import execute_prepared_action
from tests.deep_runtime.test_permission_gate import (
    APPROVAL_PERSISTENCE_MODULE,
    LEAD_SCOPE,
    MODULE,
    USER_ID,
    WORKSPACE_ID,
    _exploding_interrupt,
    _gate,
    _hook,
    _persist_db_factory,
    _request,
)
from tests.test_prepared_actions import (
    _db_factory,
    _FakeLedger,
    _Recorder,
    _registry_returning,
)

# The write the lead decides on and the read it makes afterwards. The write's capability is
# inside LEAD_SCOPE, so the replay's snapshot check passes on authority the gate itself recorded.
WRITE_TOOL = "send_email"
WRITE_CAPABILITY = "email.send"
WRITE_ARGS = {"to": "investor@example.com", "subject": "Q3 update", "body": "Numbers attached."}
READ_TOOL = "read_email"
_CAPABILITIES = {WRITE_TOOL: WRITE_CAPABILITY, READ_TOOL: "email.read"}


async def test_a_write_nobody_could_confirm_becomes_finished_reviewable_work():
    """Walk the whole arc: prepare → carry on → record → replay → exactly once."""
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        """The DB write, faked. Everything it is HANDED is the real gate's output."""
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_e2e_001")

    handler = AsyncMock(name="dispatcher")
    handler.return_value = ToolMessage(content="executed", tool_call_id="c2")
    resolve_capability = AsyncMock(side_effect=lambda name: (True, _CAPABILITIES[name]))

    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=AsyncMock(name="assess_risk"),
        db_factory=_persist_db_factory(),
        acting_agent_scope=LEAD_SCOPE,
        presence="absent",
    )

    # ── 1. An absent turn with a gated write: staged, not executed, not suspended ──────
    with (
        # An accidental suspend must be LOUD. In production it is a turn that hangs forever,
        # which a silent mock would hide.
        patch(f"{MODULE}.interrupt", _exploding_interrupt),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        staged = await _hook(mw)(_request(WRITE_TOOL, WRITE_ARGS, "c1"), handler)

        handler.assert_not_awaited()  # nothing ran
        # LOAD-BEARING: stream_adapter maps status == "error" onto the frozen `blocked` SSE
        # frame, which would stop the lead at the first staged write.
        assert staged.status == "success"
        staged_payload = json.loads(staged.content)
        assert staged_payload["prepared"] is True
        approval_id = staged_payload["approval_id"]
        assert approval_id == "apr_e2e_001"

        # ── 2. The turn keeps going: "I did these two and prepared this one" ──────────
        followup = await _hook(mw)(_request(READ_TOOL, {}, "c2"), handler)

    handler.assert_awaited_once()
    assert followup is handler.return_value

    # ── 3. What the gate persisted is reviewable AND replayable ───────────────────────
    refs = captured["artifact_refs"]
    assert captured["approval_type"] == "prepared_action"
    assert refs["prepared"] is True
    # A prepared action has NO thread to resume. The `chat` flag would route it at
    # /v1/muldro/chat/resume and make the standard approval endpoints 409 it.
    assert "chat" not in refs
    # …and the real guard, driven by the real refs, therefore stays quiet.
    _guard_not_chat_approval(SimpleNamespace(artifact_refs=refs))
    assert refs["capability_scope"] == sorted(LEAD_SCOPE)
    assert refs["capability_scope"], "an empty scope snapshot refuses at replay"
    assert refs["tool_name"] == WRITE_TOOL
    assert refs["capability"] == WRITE_CAPABILITY
    assert refs["tool_input_truncated"] is False
    assert json.loads(refs["tool_input"]) == WRITE_ARGS

    approval = SimpleNamespace(
        approval_id=approval_id,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        approval_type=captured["approval_type"],
        status="approved",
        artifact_refs=refs,
    )

    # ── 4. Confirmation replays EXACTLY that — same tool, same arguments ──────────────
    dispatcher = _Recorder()
    ledger = _FakeLedger()
    registry = _registry_returning(SimpleNamespace(name=WRITE_TOOL, capability=WRITE_CAPABILITY))

    with patch("src.services.prepared_actions.ToolRegistry", registry):
        first = await execute_prepared_action(
            approval,
            execute_tool=dispatcher,
            db_factory=_db_factory,
            redis=None,
            ledger=ledger,
        )

        assert first.executed is True, first.error
        assert dispatcher.calls == [(WRITE_TOOL, WRITE_ARGS, USER_ID, WORKSPACE_ID)]

        # ── 5. Exactly once: a founder clicking twice sees success twice, one effect ──
        second = await execute_prepared_action(
            approval,
            execute_tool=dispatcher,
            db_factory=_db_factory,
            redis=None,
            ledger=ledger,
        )

    # The guarantee is that the EFFECT happened once — asserted before the label, because that
    # is the claim a broken identity key violates. A second click still reporting success is a
    # kindness on top of it, not the guarantee.
    assert len(dispatcher.calls) == 1, "a double-confirm must not double-fire an external write"
    assert second.executed is True
    assert second.outcome == "already_executed"
