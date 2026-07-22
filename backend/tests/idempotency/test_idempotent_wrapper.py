"""The wrapper is the injected-execute_tool_fn seam. The key property: on a
'resume' (a second call for the same run/step/capability) with RECOMPOSED args,
the inner tool is called EXACTLY ONCE. Mocked ledger + injected resolver (no DB)."""

from unittest.mock import AsyncMock, MagicMock

from src.services.idempotency.ledger import ReserveOutcome
from src.services.idempotency.wrapper import IdempotencyContext, make_idempotent_execute_tool_fn


def _ctx(ledger):
    return IdempotencyContext(
        ledger=ledger, run_id="r", step_id="st", workspace_id="ws", db_factory=MagicMock()
    )


def _resolver(is_write, capability="email.send"):
    async def _resolve(tool_name, db_factory, workspace_id):
        if tool_name == "get_gmail_message_content":
            return ("email.read", False)
        return (capability, is_write)

    return _resolve


async def test_read_capability_bypasses_the_ledger():
    inner = AsyncMock(return_value={"ok": True})
    ledger = AsyncMock()
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    await fn("get_gmail_message_content", {"id": "1"}, user_id="u", workspace_id="ws")
    inner.assert_awaited_once()
    ledger.reserve.assert_not_called()


async def test_first_write_reserves_and_records():
    inner = AsyncMock(return_value={"status": "sent", "id": "msg_1"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=False,
            in_flight_conflict=False,
            result=None,
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    out = await fn(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "draft-1"},
        user_id="u",
        workspace_id="ws",
    )
    inner.assert_awaited_once()
    ledger.record_success.assert_awaited_once_with("idem_1", {"status": "sent", "id": "msg_1"})
    assert out == {"status": "sent", "id": "msg_1"}


async def test_resume_with_recomposed_args_does_not_double_fire():
    """THE acceptance property (unit proxy): the ledger says already_done, so the
    inner tool is NOT called again even though the body changed on resume."""
    inner = AsyncMock(return_value={"status": "sent", "id": "msg_1"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=True,
            in_flight_conflict=False,
            result={"status": "sent", "id": "msg_1"},
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    out = await fn(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "RECOMPOSED on resume"},
        user_id="u",
        workspace_id="ws",
    )
    inner.assert_not_awaited()  # exactly-once: the second fire is suppressed
    ledger.record_success.assert_not_called()
    assert out == {"status": "sent", "id": "msg_1"}  # returns the first-attempt result


async def test_in_flight_conflict_is_not_refired():
    inner = AsyncMock(return_value={"status": "sent"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=False,
            in_flight_conflict=True,
            result=None,
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    out = await fn(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "x"},
        user_id="u",
        workspace_id="ws",
    )
    inner.assert_not_awaited()
    assert out.get("idempotent_uncertain") is True


async def test_failed_inner_result_marks_failed():
    inner = AsyncMock(return_value={"error": "smtp down", "is_error": True})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=False,
            in_flight_conflict=False,
            result=None,
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    await fn(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "x"},
        user_id="u",
        workspace_id="ws",
    )
    ledger.mark_failed.assert_awaited_once_with("idem_1")
    ledger.record_success.assert_not_called()
