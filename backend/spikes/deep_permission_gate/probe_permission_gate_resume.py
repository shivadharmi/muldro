"""SPIKE (Chat Permission Model · P2 — DECISION GATE): does a ``permission_gate``
(interrupt on **mode × risk**, NOT auth-source) compose with the existing durable
interrupt → approve → resume machinery, for a CHAT lead + REAL sonnet + a synchronous
SSE-reconnect — such that the write executes EXACTLY ONCE on approve, is SKIPPED on
reject, and the lead emits its terminal reply either way?

Why this is a genuine unknown and not already covered by prior spikes/tests
--------------------------------------------------------------------------
The interrupt/resume PRIMITIVES exist and are proven, but never in THIS combination:
  * ``spikes/deep_stream/interrupt_replay_side_effect_probe.py`` — tool-runs-once across
    resume, but MemorySaver + FAKE model + a GENERIC gate (always interrupts) + SAME
    graph object.
  * ``spikes/deep_stream/interrupt_resume_stream_proof.py`` — ``__interrupt__``-in-updates
    pause + ``Command(resume)`` approve/reject, but MemorySaver + SCRIPTED fake + SAME
    object.
  * ``tests/test_deep_gate_durable_resume_db.py`` — durable AsyncPostgresSaver pause +
    FRESH-rebuild resume + exactly-once, but SCRIPTED fake + the ``trust_gate`` under a
    forced ``authorization_source="autonomous"``.
  * ``spikes/deep_single_lead/probe_pure_write_terminal.py`` — a REAL sonnet lead replies
    after a pure write, but with NO gate / NO pause.
  * Step 10C SQ1 — fresh-graph resume exactly-once on AsyncPostgresSaver, but a CRASH
    scenario + the idempotency LEDGER + the autonomous path.

None of them cover TOGETHER the two P2 unknowns:
  (a) A ``permission_gate`` that fires on ``mode × risk`` while the chat auth-source is
      ``DIRECT_USER_REQUEST`` (where the existing ``trust_gate`` SHORT-CIRCUITS — so a gate
      that actually pauses a chat turn is untested), composed with a FRESH-rebuild resume
      (like ``resume_deep_turn``, a new compiled graph — not the same object).
  (b) The write firing EXACTLY ONCE across that pause on the PRODUCTION substrate
      (AsyncPostgresSaver, msgpack checkpoint round-trip) WITHOUT an idempotency ledger —
      the chat deep chain has none (grep-confirmed empty in 10C). Spec §3.3 C-SEC2 only
      settled the NON-pausing bypass turn ("LangGraph never replays a write"); ask/auto
      modes DO pause, so exactly-once-without-a-ledger across an interrupt is genuinely open.
      If this REFUTES (double-fire), the ask/auto chat gate needs a ledger — a real design
      addition.
  Plus the REAL-model behavioral unknown in a NEW context: after RESUMING post-approval
  (seeing the tool result) and after a REJECTION ToolMessage, does the lead still compose a
  terminal user-facing reply?

Run (from backend/, needs MULDRO_ANTHROPIC_API_KEY in .env + USE_BEDROCK=false + a reachable
Postgres — same infra the durable resume test uses):
    uv run python spikes/deep_permission_gate/probe_permission_gate_resume.py

Exit 0 iff EVERY assertion holds (round trip composes; exactly-once on approve; skip on
reject; terminal reply on both; auto-safe passes through un-paused; predicate exhaustive).
Any failure ⇒ exit 1 with a REFUTED verdict naming the broken unknown.

THROWAWAY investigation probe. Makes real (paid) API calls + writes/reads a few Postgres
``checkpoints*`` rows (self-cleaning).

============================================================================
FINDINGS (2026-07-13 · real sonnet + real Postgres · langgraph 1.2.6 / deepagents 0.6.11):
    VERDICT: CONFIRMED ✅ — all four scenarios pass.

    predicate    PASS  — all 8 mode×risk arms correct (bypass never, ask always,
                         auto iff not-reversible/external/high).
    ask_approve  PASS  — turn-1 yields exactly ONE approval_needed frame, 0 writes while
                         paused; after pool_a.close() a FRESH saver_b + FRESH-compiled lead
                         resume via Command(resume="approve") → the write fires EXACTLY ONCE
                         (recovered from Postgres, not a surviving object) and the lead emits
                         a terminal reply ("Done — email sent to alice@… confirming the 3pm…").
    ask_reject   PASS  — same pause; Command(resume="reject") → write SKIPPED (0) + terminal
                         reply after the rejection ToolMessage.
    auto_safe    PASS  — auto + a SAFE assessment: NO pause, write executes directly, reply.

    ⇒ UNKNOWN (a) a permission_gate (interrupt on mode×risk, NOT auth-source) COMPOSES with
      the fresh-rebuild resume machinery. YES.
    ⇒ UNKNOWN (b) the paused write fires EXACTLY ONCE on approve / is SKIPPED on reject,
      on the durable AsyncPostgresSaver substrate, WITHOUT an idempotency ledger. YES —
      because interrupt() pauses BEFORE the tool node, so replay re-runs only the (idempotent)
      pre-interrupt gate body; the tool runs once, post-verdict. Spec §3.3 C-SEC2's ledger-free
      posture EXTENDS to the pausing ask/auto turn (settled here; §3.3 only covered bypass).

    DESIGN INPUTS for the P2 build:
    - ask mode skipped the risk classifier (frame risk='n/a'): confirm-every-write needs no
      RiskAssessor call — the production permission_gate should assess ONLY in auto mode.
    - the permission_gate MUST be replay-safe up to interrupt() (no side effects before it),
      exactly like trust_gate; in auto mode the risk verdict must be STABLE across replay
      (get_or_assess_risk's 24h Redis cache provides this — do NOT re-assess non-deterministically).
    - REJECT-REPLY CONFABULATION: the model invented a reason on reject because the rejection
      ToolMessage carried only {"rejected": true}. Give the rejection ToolMessage a clear,
      quotable reason (or template the reject reply) so the lead explains the rejection accurately.
    - resume rebuild used AUTONOMOUS auth for trust_gate to re-reach interrupt(); the
      permission_gate is auth-source-INDEPENDENT, so the resume rebuild does not need the
      autonomous-auth trick — it re-reaches interrupt() purely on mode×risk.
============================================================================
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Standalone script: put backend/ (three dirs up) on sys.path so ``src.*`` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncpg
from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

from src.config.settings import get_settings
from src.deep_runtime.checkpointer import build_async_postgres_saver
from src.deep_runtime.middleware.trust_gate import DEEPAGENTS_BUILTIN_NAMES
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.thread_identity import make_thread_id
from src.integrations.capabilities import is_read_only_capability
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.prompts import MULDRO_SOUL_CORE, LEAD_PROMPT, PRESENTER_VOICE
from src.services.risk_assessor import RiskAssessment

# ---------------------------------------------------------------------------
# Inert write-shaped tool. Records the call + returns a success string — no real
# external effect — but to the model it reads as a genuine write, so the react loop
# takes a real "act then what?" decision. The module-level list is the exactly-once
# witness (len == 1 after approve, len == 0 after reject / before resume).
# ---------------------------------------------------------------------------
SENT_EMAILS: list[dict] = []


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email. Returns a confirmation with the sent message id."""
    SENT_EMAILS.append({"to": to, "subject": subject, "body": body})
    return f'{{"status": "sent", "message_id": "msg_{len(SENT_EMAILS):04d}", "to": "{to}"}}'


# tool name -> capability (production resolves via the shared ToolDef resolver; stubbed here)
CAPABILITY_OF = {"send_email": "email.send"}

WS_ID = "ws_permgate_spike"
SYSTEM_TEXT = f"{MULDRO_SOUL_CORE}\n\n--- YOUR ROLE ---\n{LEAD_PROMPT}\n\n{PRESENTER_VOICE}"

ASK_EMAIL_MSG = (
    "Send an email to alice@example.com with subject 'Meeting confirmed' telling her the "
    "3pm Thursday sync is confirmed. Keep the body to one sentence."
)


# ===========================================================================
# The P2 permission predicate + the spike-local permission_gate middleware.
# AUTH-SOURCE-INDEPENDENT: it never inspects ``authorization_source`` — it pauses purely
# on the user's chosen ``mode`` crossed with the write's risk. This is the whole point:
# on chat the auth-source is DIRECT_USER_REQUEST (where trust_gate short-circuits), yet
# ask/auto must still be able to pause.
# ===========================================================================
def permission_should_interrupt(mode: str, assessment: RiskAssessment | None) -> bool:
    """Return True iff this write must pause for confirmation.

    bypass -> never; ask -> always (every write); auto -> iff the write is
    NOT reversible OR its blast_radius is external/public OR risk_level is high.
    """
    if mode == "bypass":
        return False
    if mode == "ask":
        return True
    # auto
    assert assessment is not None, "auto mode must have a RiskAssessment"
    return (
        not assessment.reversible
        or assessment.blast_radius in {"external_single", "external_multiple", "public"}
        or assessment.risk_level == "high"
    )


def make_permission_gate(mode: str, assess):  # noqa: ANN001, ANN201
    """Build a ``@wrap_tool_call`` gate that interrupts on ``mode × risk``.

    ``assess(capability, args) -> RiskAssessment`` is consulted ONLY in auto mode
    (ask never needs a classifier; bypass never pauses). A fresh gate is built for the
    resume rebuild — mirroring ``resume_deep_turn`` rebuilding a brand-new compiled graph.
    """

    @wrap_tool_call
    async def permission_gate(request, handler):  # noqa: ANN001, ANN201
        name = request.tool_call["name"]
        # deepagents built-ins (write_todos, ls, …) are framework scaffolding — never gated.
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        capability = CAPABILITY_OF.get(name)
        # reads never gate; an unknown capability falls through here (spike has only writes).
        if capability is None or is_read_only_capability(capability):
            return await handler(request)

        assessment = None
        risk_level = "n/a"
        if mode != "ask":  # ask confirms EVERY write — no risk call
            assessment = await assess(capability, request.tool_call.get("args") or {})
            risk_level = assessment.risk_level

        if not permission_should_interrupt(mode, assessment):
            return await handler(request)  # auto-safe / bypass: execute directly

        # Suspend for approval — payload shaped for stream_adapter._approval_needed_frame.
        verdict = interrupt(
            {
                "approval_id": f"apr_spike_{request.tool_call['id']}",
                "capability": capability,
                "risk_level": risk_level,
            }
        )
        approved = verdict == "approve" or (
            isinstance(verdict, dict) and verdict.get("decision") == "approve"
        )
        if approved:
            return await handler(request)
        return ToolMessage(
            content=json.dumps({"error": "rejected by approver", "rejected": True}),
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    return permission_gate


def _assess_forbidden(capability, args):  # noqa: ANN001, ANN201
    raise AssertionError("ask mode must NOT assess risk — assess() was called")


async def _assess_safe(capability, args) -> RiskAssessment:  # noqa: ANN001
    return RiskAssessment(
        risk_level="low", reasoning="spike-safe", reversible=True, blast_radius="self"
    )


async def _assess_risky(capability, args) -> RiskAssessment:  # noqa: ANN001
    return RiskAssessment(
        risk_level="high", reasoning="spike-risky", reversible=False, blast_radius="external_single"
    )


# ---------------------------------------------------------------------------
# Real-sonnet lead build + streaming driver (production adapter).
# ---------------------------------------------------------------------------
def _build_lead(checkpointer, gate):  # noqa: ANN001, ANN201
    from src.deep_runtime.model_factory import build_chat_model

    lead = SubAgent(
        name="lead",
        prompt=SYSTEM_TEXT,
        model_tier="sonnet",
        capability_scope={"email.send"},
        max_tokens=2048,
        thinking=ThinkingConfig(enabled=False),  # cheaper/faster; reply is prompt-driven
    )
    return create_deep_agent(
        model=build_chat_model(lead),
        tools=[send_email],
        middleware=[gate],
        system_prompt=SYSTEM_TEXT,
        checkpointer=checkpointer,
    )


async def _drive(agent, graph_input, thread_id: str) -> list[dict]:  # noqa: ANN001
    return [
        f
        async for f in stream_deep_agent_events(
            agent,
            graph_input,
            {"configurable": {"thread_id": thread_id}},
            agent_name="lead",
            model="claude-sonnet-5",
            durability="sync",
        )
    ]


def _terminal_reply(frames: list[dict]) -> str:
    """The lead's terminal user-facing text: the agent_done frame's text (mirrors what
    stream_deep_lead re-homes as Presentation), with a text_delta fallback."""
    for f in frames:
        if f.get("event") == "agent_done" and (f.get("text") or "").strip():
            return f["text"].strip()
    parts = [f.get("text", "") for f in frames if f.get("event") == "text_delta"]
    return "".join(parts).strip()


async def _delete_checkpoint_rows(thread_id: str) -> None:
    dsn = get_settings().database_url.replace("+asyncpg", "", 1)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    finally:
        await conn.close()


async def _db_reachable() -> bool:
    """Awaited directly (we are already inside main()'s event loop — a nested
    asyncio.run() would raise). The durable resume TEST calls its equivalent at module
    level, outside any loop; this spike checks it in-loop instead."""
    dsn = get_settings().database_url.replace("+asyncpg", "", 1)
    try:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception:
        return False


# ===========================================================================
# Scenarios
# ===========================================================================
async def scenario_ask_roundtrip(decision: str, results: dict) -> None:
    """ASK mode, REAL sonnet, durable AsyncPostgresSaver, FRESH-rebuild resume.

    Turn 1 under saver_a pauses on interrupt(); pool_a is CLOSED (the object that
    witnessed the pause is gone); a FRESH saver_b + a FRESH compiled lead resume via
    Command(resume=decision). Proves (a) composition + (b) exactly-once / skip on the
    production substrate — the write can only be recovered from Postgres, not a
    surviving in-process object.
    """
    key = f"ask_{decision}"
    label = f"ASK / resume={decision}"
    print("=" * 78)
    print(f"SCENARIO {label}  (real sonnet · AsyncPostgresSaver · fresh-rebuild resume)")
    print("=" * 78)

    db_url = get_settings().database_url
    thread_id = make_thread_id(WS_ID)
    SENT_EMAILS.clear()
    before = len(SENT_EMAILS)

    saver_a, pool_a = await build_async_postgres_saver(db_url)
    pool_a_closed = False
    pool_b = None
    try:
        agent1 = _build_lead(saver_a, make_permission_gate("ask", _assess_forbidden))
        frames1 = await _drive(
            agent1, {"messages": [{"role": "user", "content": ASK_EMAIL_MSG}]}, thread_id
        )
        approvals = [f for f in frames1 if f.get("event") == "approval_needed"]
        paused_ok = len(approvals) == 1
        not_executed_while_paused = len(SENT_EMAILS) == before
        print(
            f"  turn-1: approval_needed frames={len(approvals)} (expect 1); "
            f"writes while paused={len(SENT_EMAILS) - before} (expect 0)"
        )
        if approvals:
            af = approvals[0]
            print(
                f"          frame: capability={af.get('capability')!r} "
                f"risk={af.get('risk_level')!r} thread={af.get('thread_id')!r} "
                f"approval_id={af.get('approval_id')!r}"
            )

        # --- durability boundary: kill pool_a, resume over a FRESH saver_b ---
        await pool_a.close()
        pool_a_closed = True
        saver_b, pool_b = await build_async_postgres_saver(db_url)
        agent2 = _build_lead(saver_b, make_permission_gate("ask", _assess_forbidden))
        frames2 = await _drive(agent2, Command(resume=decision), thread_id)

        writes_after = len(SENT_EMAILS) - before
        reply = _terminal_reply(frames2)
        expect_writes = 1 if decision == "approve" else 0
        exactly_once = writes_after == expect_writes
        has_reply = bool(reply)
        print(
            f"  resume: writes after={writes_after} (expect {expect_writes}); "
            f"terminal reply present={has_reply}"
        )
        print(f"          reply :: {reply[:110].replace(chr(10), ' ')}")

        results[key] = {
            "paused_once": paused_ok,
            "not_executed_while_paused": not_executed_while_paused,
            "exactly_once_or_skipped": exactly_once,
            "terminal_reply": has_reply,
            "ok": paused_ok and not_executed_while_paused and exactly_once and has_reply,
        }
    finally:
        if not pool_a_closed:
            await pool_a.close()
        if pool_b is not None:
            await pool_b.close()
        await _delete_checkpoint_rows(thread_id)
    print()


async def scenario_auto_safe_passthrough(results: dict) -> None:
    """AUTO mode + a SAFE assessment: the gate must NOT interrupt — the write executes
    directly and the lead replies. Proves the mode×risk predicate's non-interrupt arm
    composes with the react loop (auto does not over-prompt). MemorySaver (no durability
    boundary is under test here)."""
    from langgraph.checkpoint.memory import MemorySaver

    print("=" * 78)
    print("SCENARIO AUTO / safe write  (gate passes through, no pause)")
    print("=" * 78)
    thread_id = "spike-auto-safe"
    SENT_EMAILS.clear()
    before = len(SENT_EMAILS)
    agent = _build_lead(MemorySaver(), make_permission_gate("auto", _assess_safe))
    graph_input = {"messages": [{"role": "user", "content": ASK_EMAIL_MSG}]}
    frames = await _drive(agent, graph_input, thread_id)
    approvals = [f for f in frames if f.get("event") == "approval_needed"]
    writes = len(SENT_EMAILS) - before
    reply = _terminal_reply(frames)
    no_pause = len(approvals) == 0
    executed = writes == 1
    has_reply = bool(reply)
    print(
        f"  approval_needed frames={len(approvals)} (expect 0); writes={writes} (expect 1); "
        f"reply={has_reply}"
    )
    print(f"  reply :: {reply[:110].replace(chr(10), ' ')}")
    results["auto_safe"] = {
        "no_pause": no_pause,
        "executed_once": executed,
        "terminal_reply": has_reply,
        "ok": no_pause and executed and has_reply,
    }
    print()


def scenario_predicate_exhaustive(results: dict) -> None:
    """Pure, free, deterministic check of the mode×risk predicate over every arm."""
    print("=" * 78)
    print("SCENARIO predicate (pure): mode × risk -> interrupt?")
    print("=" * 78)
    safe = RiskAssessment(risk_level="low", reasoning="", reversible=True, blast_radius="self")
    irreversible = RiskAssessment(
        risk_level="low", reasoning="", reversible=False, blast_radius="self"
    )
    external = RiskAssessment(
        risk_level="low", reasoning="", reversible=True, blast_radius="external_single"
    )
    high = RiskAssessment(risk_level="high", reasoning="", reversible=True, blast_radius="self")
    cases = [
        ("bypass", safe, False),
        ("bypass", high, False),
        ("ask", safe, True),
        ("ask", None, True),
        ("auto", safe, False),
        ("auto", irreversible, True),
        ("auto", external, True),
        ("auto", high, True),
    ]
    all_ok = True
    for mode, assessment, expected in cases:
        got = permission_should_interrupt(mode, assessment)
        ok = got == expected
        all_ok = all_ok and ok
        tag = "ok " if ok else "BAD"
        print(f"  [{tag}] mode={mode:<7} -> interrupt={got} (expect {expected})")
    results["predicate"] = {"ok": all_ok}
    print()


async def main() -> int:
    settings = get_settings()
    if settings.use_bedrock:
        print("SKIP: USE_BEDROCK=true — this probe needs the direct Anthropic API.")
        return 0
    if not settings.anthropic_api_key:
        print("SKIP: MULDRO_ANTHROPIC_API_KEY not set.")
        return 0
    if not await _db_reachable():
        print("SKIP: Postgres not reachable (needed for the durable AsyncPostgresSaver proof).")
        return 0
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)

    print("#" * 78)
    print("# P2 PERMISSION-GATE interrupt->approve->resume SPIKE (REAL sonnet + real PG)")
    print("#" * 78)
    print()

    results: dict = {}
    scenario_predicate_exhaustive(results)
    await scenario_ask_roundtrip("approve", results)
    await scenario_ask_roundtrip("reject", results)
    await scenario_auto_safe_passthrough(results)

    # ------------------------------------------------------------------ SUMMARY
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for k, v in results.items():
        print(f"  {k:<14} {'PASS' if v['ok'] else 'FAIL'}  {v}")

    ask_approve = results.get("ask_approve", {})
    ask_reject = results.get("ask_reject", {})
    unknown_a_ok = ask_approve.get("paused_once") and ask_approve.get("exactly_once_or_skipped")
    unknown_b_ok = ask_approve.get("exactly_once_or_skipped") and ask_reject.get(
        "exactly_once_or_skipped"
    )
    reply_ok = ask_approve.get("terminal_reply") and ask_reject.get("terminal_reply")
    all_ok = all(v.get("ok") for v in results.values())

    print()
    print(f"  UNKNOWN (a) gate composes w/ fresh-rebuild resume : {bool(unknown_a_ok)}")
    print(f"  UNKNOWN (b) exactly-once approve / skip reject    : {bool(unknown_b_ok)}")
    print(f"  real-model terminal reply on approve AND reject   : {bool(reply_ok)}")
    print(f"  auto-safe passes through un-paused                : "
          f"{results.get('auto_safe', {}).get('ok')}")
    print()
    if all_ok:
        print("VERDICT: CONFIRMED ✅ — the permission_gate interrupt->approve->resume round trip "
              "works end-to-end for a chat lead on the durable substrate. P2 is safe to design "
              "on top of the existing resume machinery (no ledger required for the paused write).")
        return 0
    print("VERDICT: REFUTED ❌ — see the FAIL rows above. Redesign before building "
          "(double-fire ⇒ chat gate needs a ledger; no reply ⇒ forced-synthesis fallback; "
          "no pause/resume ⇒ the gate does not compose).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
