"""Both write gates persist the redacted tool_input onto the Approval (legibility, step 1).

An approval card that says "Approve: email.send" and cannot show the email is not a decision
the founder can actually make. These tests pin the persisted shape for BOTH gates.
"""

from unittest.mock import AsyncMock, patch

from src.deep_runtime.middleware import permission_gate as pg
from src.deep_runtime.middleware import trust_gate as tg
from src.deep_runtime.middleware.trust_gate import _MAX_PERSISTED_CONTEXT_CHARS
from src.services.risk_assessor import RiskAssessment
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


class _FakeDecision:
    decision = "approval_required"
    justification = "needs a human"


async def test_trust_gate_persists_redacted_tool_input():
    captured = {}

    async def _fake_get_or_create(db, **kwargs):
        captured.update(kwargs)
        return "apr_test_1"

    class _FakeDbCtx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *exc):
            return False

    with (
        patch.object(tg, "_get_or_create_approval", _fake_get_or_create),
        patch.object(tg, "TrustEngine") as trust_engine,
        patch.object(tg, "is_write_verification_required", return_value=False),
    ):
        trust_engine.return_value.evaluate = AsyncMock(return_value=_FakeDecision())
        require, approval_id = await tg._decide_and_maybe_persist(
            name="gmail_send_email",
            capability="email.send",
            risk=RiskAssessment(
                risk_level="medium", reasoning="r", reversible=False, blast_radius="external_single"
            ),
            workspace_id=TEST_WORKSPACE_ID,
            user_id=TEST_USER_ID,
            thread_id="thr_1",
            tool_call_id="call_1",
            agent_name="executor",
            db_factory=lambda: _FakeDbCtx(),
            context_block="",
            tool_input={"to": "a@b.com", "api_key": "sk-live"},
            agent_capability_scope=frozenset({"email.send", "email.read"}),
            presence="present",
        )

    assert require is True
    assert approval_id == "apr_test_1"
    refs = captured["artifact_refs"]
    assert '"to": "a@b.com"' in refs["tool_input"]
    assert "sk-live" not in refs["tool_input"]
    assert refs["tool_input_truncated"] is False
    assert refs["capability_scope"] == ["email.read", "email.send"]
    assert refs["effective_presence"] == "present"


async def test_permission_gate_persists_redacted_tool_input():
    captured = {}

    async def _fake_get_or_create(db, **kwargs):
        captured.update(kwargs)
        return "apr_test_2"

    class _FakeDbCtx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *exc):
            return False

    with patch.object(pg, "_get_or_create_approval", _fake_get_or_create):
        approval_id = await pg._persist_permission_approval(
            name="gmail_send_email",
            capability="email.send",
            assessment=None,
            risk_level="n/a",
            workspace_id=TEST_WORKSPACE_ID,
            user_id=TEST_USER_ID,
            thread_id="thr_2",
            tool_call_id="call_2",
            agent_name="lead",
            db_factory=lambda: _FakeDbCtx(),
            context_block="",
            permission_mode="ask",
            acting_agent_scope=frozenset({"email.send"}),
            user_message="send it",
            tool_input={"to": "a@b.com", "authorization": "Bearer x"},
            presence="present",
        )

    assert approval_id == "apr_test_2"
    refs = captured["artifact_refs"]
    assert '"to": "a@b.com"' in refs["tool_input"]
    assert "Bearer x" not in refs["tool_input"]
    assert refs["capability_scope"] == ["email.send"]
    # A PRESENT-user chat approval still carries the chat flag (it resumes via /chat/resume).
    assert refs["chat"] is True


async def test_presence_defaults_to_absent_when_a_caller_omits_it():
    """The default is the FAIL-SAFE direction and is about to become authority-bearing:
    a later task makes `absent` mean "prepare this write for review" rather than "interrupt".
    A caller that forgets to pass presence must land on the safe side, so pin it here."""
    captured = {}

    async def _fake_get_or_create(db, **kwargs):
        captured.update(kwargs)
        return "apr_test_3"

    class _FakeDbCtx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *exc):
            return False

    with patch.object(pg, "_get_or_create_approval", _fake_get_or_create):
        await pg._persist_permission_approval(
            name="gmail_send_email",
            capability="email.send",
            assessment=None,
            risk_level="n/a",
            workspace_id=TEST_WORKSPACE_ID,
            user_id=TEST_USER_ID,
            thread_id="thr_3",
            tool_call_id="call_3",
            agent_name="lead",
            db_factory=lambda: _FakeDbCtx(),
            context_block="",
            permission_mode="ask",
            acting_agent_scope=frozenset({"email.send"}),
            # presence deliberately NOT passed
        )

    assert captured["artifact_refs"]["effective_presence"] == "absent"


async def test_an_oversized_payload_is_persisted_truncated_and_flagged():
    """The flag is what a later task keys "refuse to replay this" on, so it has to be
    written correctly at persist time, not just computed correctly in the helper."""
    captured = {}

    async def _fake_get_or_create(db, **kwargs):
        captured.update(kwargs)
        return "apr_test_4"

    class _FakeDbCtx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *exc):
            return False

    with patch.object(pg, "_get_or_create_approval", _fake_get_or_create):
        await pg._persist_permission_approval(
            name="gmail_send_email",
            capability="email.send",
            assessment=None,
            risk_level="n/a",
            workspace_id=TEST_WORKSPACE_ID,
            user_id=TEST_USER_ID,
            thread_id="thr_4",
            tool_call_id="call_4",
            agent_name="lead",
            db_factory=lambda: _FakeDbCtx(),
            context_block="",
            permission_mode="ask",
            acting_agent_scope=frozenset({"email.send"}),
            tool_input={"to": "a@b.com", "body": "x" * 9000},
            presence="present",
        )

    refs = captured["artifact_refs"]
    assert refs["tool_input_truncated"] is True
    assert len(refs["tool_input"]) <= _MAX_PERSISTED_CONTEXT_CHARS
