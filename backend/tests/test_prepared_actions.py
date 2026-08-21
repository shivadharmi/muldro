"""Deterministic replay of a PREPARED action (single-lead cutover, Task 5).

These tests pin the promise the review queue makes: confirming a prepared action runs the
EXACT tool call the founder reviewed, or refuses. Nothing here builds an agent — an agent
would rediscover tools and decide again, re-deriving the action instead of executing it.

The three fail-closed checks reproduce what the ``capability_scope`` middleware enforces on
the agent path, against the scope SNAPSHOT taken at prepare time.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from src.services.prepared_actions import execute_prepared_action, prepared_identity_key

_DEFAULT_INPUT = {"to": "a@b.com", "subject": "hi"}
WORKSPACE_ID = "ws_prepared"
USER_ID = "usr_founder"


def _approval(
    *,
    tool_name="send_email",
    capability="email.send",
    tool_input=None,
    truncated=False,
    scope=("email.send", "knowledge.search"),
    approval_id="apr_001",
):
    refs = {
        "tool_name": tool_name,
        "capability": capability,
        "tool_input": json.dumps(_DEFAULT_INPUT if tool_input is None else tool_input),
        "tool_input_truncated": truncated,
        "capability_scope": list(scope) if scope is not None else None,
        "prepared": True,
    }
    return SimpleNamespace(
        approval_id=approval_id,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        approval_type="prepared_action",
        artifact_refs=refs,
    )


@asynccontextmanager
async def _session():
    yield SimpleNamespace()


def _db_factory():
    return _session()


def _registry_returning(tool):
    """A ToolRegistry stand-in whose get_tool always returns ``tool``."""

    class _Registry:
        def __init__(self, db, workspace_id=None):
            self.db = db
            self.workspace_id = workspace_id

        async def get_tool(self, name):
            return tool

    return _Registry


class _Recorder:
    """Stands in for ``ToolExecutor.execute_tool`` — same positional contract."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result if result is not None else {"status": "ok", "id": "msg_1"}

    async def __call__(self, tool_name, tool_input, user_id, workspace_id):
        self.calls.append((tool_name, tool_input, user_id, workspace_id))
        return self._result


class _FakeLedger:
    """In-memory IdempotencyLedger with the real reserve/record_success/mark_failed contract."""

    def __init__(self):
        self.rows = {}

    async def reserve(self, *, workspace_id, run_id, step_id, capability, identity_key):
        row = self.rows.get(identity_key)
        if row is None:
            self.rows[identity_key] = {
                "ledger_id": f"idem_{len(self.rows)}",
                "status": "in_flight",
                "result": None,
            }
            row = self.rows[identity_key]
            return SimpleNamespace(
                already_done=False,
                in_flight_conflict=False,
                result=None,
                identity_key=identity_key,
                ledger_id=row["ledger_id"],
            )
        if row["status"] == "completed":
            return SimpleNamespace(
                already_done=True,
                in_flight_conflict=False,
                result=row["result"],
                identity_key=identity_key,
                ledger_id=row["ledger_id"],
            )
        return SimpleNamespace(
            already_done=False,
            in_flight_conflict=True,
            result=None,
            identity_key=identity_key,
            ledger_id=row["ledger_id"],
        )

    async def record_success(self, ledger_id, result):
        for row in self.rows.values():
            if row["ledger_id"] == ledger_id:
                row["status"] = "completed"
                row["result"] = result

    async def mark_failed(self, ledger_id):
        for row in self.rows.values():
            if row["ledger_id"] == ledger_id:
                row["status"] = "failed"


async def _run(approval, tool, execute_tool, ledger=None):
    with patch("src.services.prepared_actions.ToolRegistry", _registry_returning(tool)):
        return await execute_prepared_action(
            approval,
            execute_tool=execute_tool,
            db_factory=_db_factory,
            redis=None,
            ledger=ledger,
        )


async def test_the_recorded_payload_is_executed_verbatim():
    """The founder reviewed THIS tool with THESE arguments — that is what must fire."""
    approval = _approval(tool_input={"to": "a@b.com", "subject": "hi", "body": "there"})
    tool = SimpleNamespace(name="send_email", capability="email.send")
    recorder = _Recorder()

    outcome = await _run(approval, tool, recorder)

    assert outcome.executed is True
    assert outcome.error is None
    assert recorder.calls == [
        (
            "send_email",
            {"to": "a@b.com", "subject": "hi", "body": "there"},
            USER_ID,
            WORKSPACE_ID,
        )
    ]


async def test_an_unknown_tool_refuses():
    recorder = _Recorder()
    outcome = await _run(_approval(), None, recorder)

    assert outcome.executed is False
    assert "unknown tool" in outcome.error
    assert recorder.calls == []


async def test_a_capability_less_tool_refuses():
    """A tool with no capability cannot be scope-checked, so it cannot be replayed."""
    tool = SimpleNamespace(name="send_email", capability=None)
    recorder = _Recorder()

    outcome = await _run(_approval(), tool, recorder)

    assert outcome.executed is False
    assert "no capability" in outcome.error
    assert recorder.calls == []


async def test_registry_drift_refuses():
    """The tool name now maps to a DIFFERENT capability than the founder reviewed."""
    tool = SimpleNamespace(name="send_email", capability="calendar.create")
    recorder = _Recorder()

    outcome = await _run(_approval(capability="email.send"), tool, recorder)

    assert outcome.executed is False
    assert "drift" in outcome.error
    assert recorder.calls == []


async def test_a_capability_outside_the_snapshotted_scope_refuses():
    """The snapshot is the authority. A scope that has since NARROWED correctly refuses, and a
    scope that has since WIDENED can never retroactively authorise this row."""
    tool = SimpleNamespace(name="send_email", capability="email.send")
    recorder = _Recorder()

    outcome = await _run(_approval(scope=("knowledge.search",)), tool, recorder)

    assert outcome.executed is False
    assert "authority" in outcome.error
    assert recorder.calls == []


async def test_a_missing_scope_snapshot_refuses():
    tool = SimpleNamespace(name="send_email", capability="email.send")
    recorder = _Recorder()

    for scope in (None, ()):
        outcome = await _run(_approval(scope=scope), tool, recorder)
        assert outcome.executed is False
        assert "scope" in outcome.error
    assert recorder.calls == []


async def test_a_clipped_payload_refuses_even_when_it_still_parses():
    """The FLAG is authoritative, never the parse.

    A clipped payload can still parse into a DIFFERENT, smaller action than the founder
    reviewed — clip a nested object at the wrong byte and the remainder is valid JSON for a
    narrower call. So this payload is deliberately valid JSON: a parse-based implementation
    would happily execute it.
    """
    approval = _approval(tool_input={"to": "a@b.com"}, truncated=True)
    json.loads(approval.artifact_refs["tool_input"])  # the payload really does parse
    tool = SimpleNamespace(name="send_email", capability="email.send")
    recorder = _Recorder()

    outcome = await _run(approval, tool, recorder)

    assert outcome.executed is False
    assert "clipped" in outcome.error
    assert recorder.calls == []


async def test_a_double_confirm_fires_exactly_once():
    """One Approval == one reviewed action, so the ledger keys on the approval id."""
    approval = _approval(approval_id="apr_double")
    tool = SimpleNamespace(name="send_email", capability="email.send")
    recorder = _Recorder()
    ledger = _FakeLedger()

    first = await _run(approval, tool, recorder, ledger=ledger)
    second = await _run(approval, tool, recorder, ledger=ledger)

    assert first.executed is True
    assert second.executed is True
    assert len(recorder.calls) == 1
    assert prepared_identity_key("apr_double") in ledger.rows


async def test_a_tool_error_marks_the_action_failed_with_its_reason():
    """A tool that returns an error dict is NOT a success, and must not consume the identity.

    The ledger row is marked failed rather than completed, so the founder can confirm again
    once whatever the tool complained about is fixed — the write never happened.
    """
    approval = _approval(approval_id="apr_tool_err")
    tool = SimpleNamespace(capability="email.send")
    ledger = _FakeLedger()
    recorder = _Recorder(result={"error": "recipient mailbox is full"})

    result = await _run(approval, tool, recorder, ledger=ledger)

    assert result.executed is False
    assert result.outcome == "tool_failed"
    assert "recipient mailbox is full" in result.error
    assert result.result == {"error": "recipient mailbox is full"}
    assert len(recorder.calls) == 1
    row = ledger.rows[prepared_identity_key("apr_tool_err")]
    assert row["status"] == "failed", "a failed tool must not leave a completed ledger row"


async def test_a_transient_refusal_is_labelled_transient_not_failed():
    """In-flight contention is RETRYABLE. The outcome must say so — the route keys on it to
    leave the row ``pending``; flattening it to a boolean permanently discards the action."""
    approval = _approval(approval_id="apr_inflight")
    tool = SimpleNamespace(capability="email.send")
    ledger = _FakeLedger()
    # First reserve leaves the row in_flight; the second confirm collides with it.
    await _run(approval, tool, _Recorder(), ledger=ledger)
    ledger.rows[prepared_identity_key("apr_inflight")]["status"] = "in_flight"

    result = await _run(approval, tool, _Recorder(), ledger=ledger)

    assert result.outcome == "transient"
    assert result.executed is False
