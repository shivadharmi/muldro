"""The presence vocabulary (single-lead cutover, invariant 1).

`presence` may only ever DOWNGRADE authority. There is no (mode, presence) pair for which
presence grants something `permission_mode` did not.
"""

import json

import pytest
from langchain_core.messages import ToolMessage

from src.deep_runtime.confirmation import (
    prepared_tool_message,
    resolve_confirmation,
    resolve_effective_permission_mode,
)
from src.deep_runtime.stream_adapter import is_blocked_result


@pytest.mark.parametrize(
    ("mode", "presence", "entitled", "expected"),
    [
        # bypass survives ONLY with a present user AND the workspace entitlement.
        ("bypass", "present", True, "bypass"),
        ("bypass", "present", False, "auto"),
        ("bypass", "absent", True, "auto"),
        ("bypass", "absent", False, "auto"),
        # ask/auto are unaffected by presence and by the bypass entitlement.
        ("ask", "present", True, "ask"),
        ("ask", "absent", True, "ask"),
        ("ask", "present", False, "ask"),
        ("ask", "absent", False, "ask"),
        ("auto", "present", True, "auto"),
        ("auto", "absent", True, "auto"),
        ("auto", "present", False, "auto"),
        ("auto", "absent", False, "auto"),
        # Unknown modes fail CLOSED to the strictest mode, never to bypass.
        ("", "present", True, "ask"),
        ("nonsense", "present", True, "ask"),
        (None, "present", True, "ask"),
        # Unknown presence is not "present" — so it cannot keep bypass.
        ("bypass", "", True, "auto"),
        ("bypass", "unknown", True, "auto"),
    ],
)
def test_presence_never_escalates(mode, presence, entitled, expected):
    assert resolve_effective_permission_mode(mode, presence, bypass_entitled=entitled) == expected


def test_no_pair_ever_produces_bypass_without_a_present_entitled_user():
    """Invariant 1 as a PROPERTY, not a table of examples: whatever the inputs, a `bypass`
    result implies all three preconditions held. Stated this way it survives someone
    rewriting the function, because it never mentions the function's branches.

    The second assertion pins the other half of the safety story — an unrecognised mode must
    land on the STRICTEST mode, never a laxer one. Without it this loop stays green when the
    fail-closed target is inverted (verified by mutation), which would make it look like
    broader cover than it is."""
    for mode in ("bypass", "ask", "auto", "", "weird", None):
        for presence in ("present", "absent", "", "weird"):
            for entitled in (True, False):
                result = resolve_effective_permission_mode(mode, presence, bypass_entitled=entitled)
                assert result in ("bypass", "ask", "auto")
                if result == "bypass":
                    assert mode == "bypass"
                    assert presence == "present"
                    assert entitled is True
                if mode not in ("bypass", "ask", "auto"):
                    assert result == "ask", "an unrecognised mode must fail CLOSED"


def test_only_a_present_human_interrupts():
    assert resolve_confirmation("present") == "interrupt"
    assert resolve_confirmation("absent") == "prepare"
    # Anything that is not the literal "present" PREPARES (fail-safe).
    assert resolve_confirmation("") == "prepare"
    assert resolve_confirmation("Present") == "prepare"
    assert resolve_confirmation("unknown") == "prepare"


def test_prepared_tool_message_is_success_not_error():
    """LOAD-BEARING: stream_adapter maps status=="error" to the frozen `blocked` SSE frame,
    which would stop the lead at the first prepared write. A prepared write is staged, not
    failed — the turn must carry on and report what it staged."""
    msg = prepared_tool_message(
        name="gmail_send_email",
        tool_call_id="call_1",
        approval_id="apr_1",
        capability="email.send",
    )
    assert msg.status == "success"
    assert msg.tool_call_id == "call_1"
    assert msg.name == "gmail_send_email"
    body = json.loads(msg.content)
    assert body["prepared"] is True
    assert body["approval_id"] == "apr_1"
    assert body["capability"] == "email.send"
    # It must not read as an error to a model consuming the transcript.
    assert "error" not in body
    # It must say plainly that nothing ran.
    assert "not executed" in body["detail"].lower()


def test_a_prepared_write_does_not_read_as_blocked_to_the_client():
    """The link between ``prepared_tool_message``'s status and the client's stop condition,
    asserted rather than commented.

    The test above pins the status to the literal ``"success"``; this one pins what that
    literal BUYS, by feeding a real prepared ToolMessage into the real predicate the stream
    adapter uses. Flipping that status to ``"error"`` makes this fail, which is the only
    automated warning a future reader gets before reintroducing the freeze.
    """
    msg = prepared_tool_message(
        name="gmail_send_email", tool_call_id="c1", approval_id="apr_1", capability="email.send"
    )
    assert is_blocked_result(msg) is False


def test_a_real_failure_still_reads_as_blocked_to_the_client():
    """The positive control. Without it the test above passes against a predicate that has
    been broken into always returning False — which would ALSO stop reporting real tool
    failures to the client, silently."""
    failed = ToolMessage(content="boom", tool_call_id="c1", name="gmail_send_email", status="error")
    assert is_blocked_result(failed) is True
    # A message with no status at all is not a failure (the adapter sees these).
    assert is_blocked_result(ToolMessage(content="ok", tool_call_id="c2")) is False
