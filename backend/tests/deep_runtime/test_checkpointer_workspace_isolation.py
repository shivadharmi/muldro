"""A6 (Step-10A): workspace-bound checkpointer thread_id + resume-side cross-workspace
isolation guard.

The LangGraph ``AsyncPostgresSaver`` keys checkpoints by ``thread_id`` alone and asserts NO
tenant ownership. ``src.deep_runtime.thread_identity`` embeds the workspace into the
``thread_id`` at mint time; ``AgentInvoker.resume_deep_turn`` asserts it on resume as
defense-in-depth on top of the existing ``approval.workspace_id`` IDOR guard (see
``tests/test_agent_invoker_resume.py::test_resume_cross_tenant_approval_is_not_found_and_never_streams``,
which covers the pre-existing guard this task adds a SECOND, independent layer on top of).

Uses the deterministic fake-db pattern (no real Postgres): the new guard fires BEFORE the
checkpointer is ever touched, so a real ``AsyncPostgresSaver`` would exercise nothing extra;
the fake-db pattern matches the canonical resume-test idiom in ``test_agent_invoker_resume.py``
and never contributes to the skip count.
"""

from unittest.mock import patch

from src.deep_runtime.thread_identity import make_thread_id, workspace_of_thread_id
from src.models.ids import generate_id
from tests.test_agent_invoker_resume import _fake_approval, _make_invoker_with_approval


def test_make_thread_id_round_trips_to_the_minting_workspace():
    assert workspace_of_thread_id(make_thread_id("ws_A")) == "ws_A"


def test_make_thread_id_is_not_attributable_to_a_different_workspace():
    assert workspace_of_thread_id(make_thread_id("ws_A")) != "ws_B"


def test_make_thread_id_fits_approval_thread_id_column_with_a_realistic_workspace_id():
    # A REALISTIC ws id (generate_id("ws") -> 29 chars), not the 4-char "ws_A" stub, which
    # would pass this length check trivially. Approval.thread_id is String(64); this format
    # is 58 chars. Overflowing this bound would force a migration Step-10A forbids.
    ws = generate_id("ws")
    thread_id = make_thread_id(ws)
    assert len(thread_id) <= 64


def test_workspace_of_thread_id_never_raises_on_a_legacy_colonless_id():
    # Defensive parse, load-bearing: resume refuses any thread whose workspace cannot be
    # recovered rather than raising.
    assert workspace_of_thread_id("legacy_colonless_id") is None


def test_workspace_of_thread_id_never_raises_on_none_or_empty():
    # A6 review (Minor 1): the module contract is "never raises", and the 10C/B9 reuse target
    # reads Approval.thread_id — a NULLABLE column — so None/empty MUST parse to None
    # (fail-closed), never AttributeError.
    assert workspace_of_thread_id(None) is None
    assert workspace_of_thread_id("") is None


async def test_resume_deep_turn_refuses_a_thread_id_minted_for_another_workspace():
    """The pre-existing :695 guard (approval.workspace_id == caller workspace_id) PASSES
    here on purpose — the approval genuinely belongs to ws_B — so a refusal proves it comes
    from the NEW thread_id-embedded-workspace guard, not the old one."""
    approval = _fake_approval(
        thread_id=make_thread_id("ws_A"),
        agent_name="perceiver",
        workspace_id="ws_B",
    )
    inv, fake_db = _make_invoker_with_approval(approval)

    with patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_stream:
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_x",
                decision="approve",
                user_id="u",
                workspace_id="ws_B",
            )
        ]

    assert any(f["event"] == "error" and f.get("message") == "approval not found" for f in frames)
    mock_stream.assert_not_called()
    # the approval was NOT consumed
    assert approval.status == "pending"
    assert approval.approved_by is None
    fake_db.commit.assert_not_awaited()
