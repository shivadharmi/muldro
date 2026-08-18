"""SPIKE (Step 10D · A-5 / 5a — DECISION GATE): does a deepagents react-loop lead
reliably emit a TERMINAL user-facing message AFTER a pure write?

This is the single genuine behavioral unknown behind the A-5 single-lead restructure.
Every prior repo spike used a SCRIPTED fake model (offline, deterministic) — but a
scripted model cannot answer this question, because the question *is* the real model's
behavior: after Claude calls a write-shaped tool and receives the tool result, does it
compose one more natural-language message to the user (the confirmation), or does it end
the turn on the raw tool result / an empty message?

The A-5 design claims ``LEAD_PROMPT``'s always-reply rule + ``PRESENTER_VOICE`` FORCE the
terminal message. This probe PROVES or REFUTES that with the REAL Anthropic API, using the
EXACT production prompt composition (MULDRO_SOUL_CORE + LEAD_PROMPT + PRESENTER_VOICE) that
``stream_deep_lead`` will build.

Run (from backend/, needs MULDRO_ANTHROPIC_API_KEY in .env, USE_BEDROCK=false):
    uv run python spikes/deep_single_lead/probe_pure_write_terminal.py

Exit 0 iff the terminal-message rule holds on EVERY main-condition run (pure-write and
read+write). A single reply-less main-condition turn ⇒ exit 1 (design needs a fallback:
a forced synthesis turn after the last tool call). The control condition (LEAD_PROMPT with
the always-reply rule REMOVED) is measured to show the rule is load-bearing, but does NOT
gate the exit code.

THROWAWAY investigation probe. Makes real (paid) API calls.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Standalone script: put backend/ (two dirs up) on sys.path so ``src.*`` resolves
# regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from src.config.settings import get_settings
from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.prompts import MULDRO_SOUL_CORE, PRESENTER_VOICE

# ---------------------------------------------------------------------------
# Draft LEAD_PROMPT under test. If this probe passes, this exact text is promoted
# verbatim into src/orchestrator/prompts.py (5a). The <always_reply> block is the
# load-bearing terminal-message rule.
# ---------------------------------------------------------------------------
LEAD_PROMPT_DRAFT = """\
<role>
You are Muldro handling a user's request from start to finish. Unlike the specialized
sub-agents, you own the WHOLE turn: gather whatever information you need using your tools,
take any actions the request calls for, and then speak to the user yourself. You are the
only voice the user hears this turn.
</role>

<how_you_work>
1. Read the request and any context you are given. Decide what to gather and what to do.
2. Use your tools to gather information (email, calendar, knowledge, and so on) and to take
   the actions the request calls for (send, create, update).
3. Work only within the capabilities you have been given. If the request needs a capability
   you do not have, say so plainly instead of pretending.
</how_you_work>

<always_reply>
You MUST end EVERY turn with a natural-language reply addressed to the user — always,
without exception. This holds even when your final step was an action: after a tool result
comes back (for example after sending an email or creating an event), write ONE more message
that tells the user, in plain language, what you did and what it means for them. NEVER end
your turn on a raw tool result or with an empty message. If you took an action, confirm it.
If you only gathered information, answer the question. The turn is not complete until you
have spoken to the user.
</always_reply>
"""

# Control: identical role, but the always-reply rule is GONE. Used to show the rule is
# load-bearing (does the model still confirm on its own?).
LEAD_PROMPT_CONTROL = """\
<role>
You are Muldro handling a user's request from start to finish. You own the WHOLE turn:
gather whatever information you need using your tools, take any actions the request calls
for. You are the only voice the user hears this turn.
</role>

<how_you_work>
1. Read the request and any context you are given. Decide what to gather and what to do.
2. Use your tools to gather information and to take the actions the request calls for.
3. Work only within the capabilities you have been given.
</how_you_work>
"""


# ---------------------------------------------------------------------------
# Inert write-shaped tools. They RECORD the call and return a success string — no real
# external effect — but to the model they read as genuine writes, so the react loop takes
# a real "act then what?" decision.
# ---------------------------------------------------------------------------
SENT_EMAILS: list[dict] = []
CREATED_EVENTS: list[dict] = []
READS: list[str] = []


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email. Returns a confirmation with the sent message id."""
    SENT_EMAILS.append({"to": to, "subject": subject, "body": body})
    return f'{{"status": "sent", "message_id": "msg_{len(SENT_EMAILS):04d}", "to": "{to}"}}'


@tool
def create_calendar_event(title: str, start: str, attendees: str) -> str:
    """Create a calendar event. Returns a confirmation with the event id."""
    CREATED_EVENTS.append({"title": title, "start": start, "attendees": attendees})
    return f'{{"status": "created", "event_id": "evt_{len(CREATED_EVENTS):04d}"}}'


@tool
def search_inbox(query: str) -> str:
    """Search the user's inbox. Returns matching messages."""
    READS.append(query)
    return (
        '{"results": [{"from": "alice@example.com", "subject": "Re: Q3 planning", '
        '"snippet": "Can we move the sync to 3pm Thursday?"}]}'
    )


def _build_lead(system_text: str) -> object:
    """Build a REAL sonnet deep agent with the given system prompt + all tools."""
    lead = SubAgent(
        name="lead",
        prompt=system_text,
        model_tier="sonnet",
        capability_scope={"email.send", "calendar.create", "email.search"},
        max_tokens=2048,
        # Disable thinking to keep the probe cheap + fast; the terminal-message behavior
        # is prompt-driven, not thinking-driven. (Production keeps thinking on — if the
        # rule holds without thinking, it holds a-fortiori with it.)
        thinking=ThinkingConfig(enabled=False),
    )
    return create_deep_agent(
        model=build_chat_model(lead),
        tools=[send_email, create_calendar_event, search_inbox],
        system_prompt=system_text,
        checkpointer=MemorySaver(),
    )


def _final_reply_text(result: dict) -> str:
    """Extract the terminal user-facing text: the LAST AIMessage with non-empty text
    content AND no pending tool calls. Mirrors what chat_processor yields as the reply
    (frame ``agent_done`` text). Returns '' if the turn ended on a tool result / empty."""
    messages = result.get("messages", [])
    if not messages:
        return ""
    last = messages[-1]
    # The turn is reply-less if the last message is a ToolMessage (ended on a tool result)
    # or an AIMessage that only issued tool calls with no text.
    if isinstance(last, ToolMessage):
        return ""
    if isinstance(last, AIMessage):
        if last.tool_calls:
            return ""  # ended wanting to call another tool — not a terminal reply
        return _text_of(last)
    return ""


def _text_of(msg: AIMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content.strip()
    # Anthropic content blocks: list of dicts; concatenate text blocks.
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts).strip()


def _tool_was_called(result: dict, tool_name: str) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == tool_name for m in result.get("messages", [])
    )


# (scenario label, user message, expected write tool name)
SCENARIOS = [
    (
        "pure-write:email",
        "Send an email to alice@example.com with subject 'Meeting confirmed' telling her "
        "the 3pm Thursday sync is confirmed.",
        "send_email",
    ),
    (
        "pure-write:calendar",
        "Create a calendar event titled 'Q3 sync' at 3pm Thursday with alice@example.com.",
        "create_calendar_event",
    ),
    (
        "read+write",
        "Check my inbox for alice's scheduling request and then send her an email confirming "
        "whatever time she proposed.",
        "send_email",
    ),
]

N_RUNS = 4  # runs per scenario (probabilistic model — measure a rate, not a single sample)


async def _run_one(agent: object, user_message: str, idx: int) -> dict:
    config = {"configurable": {"thread_id": f"spike-{idx}"}}
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_message}]}, config
    )
    return result


async def main() -> int:
    settings = get_settings()
    if settings.use_bedrock:
        print("SKIP: USE_BEDROCK=true — this probe needs the direct Anthropic API.")
        return 0
    if not settings.anthropic_api_key:
        print("SKIP: MULDRO_ANTHROPIC_API_KEY not set.")
        return 0
    # ChatAnthropic reads ANTHROPIC_API_KEY from env; bridge it from settings.
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)

    main_system = f"{MULDRO_SOUL_CORE}\n\n--- YOUR ROLE ---\n{LEAD_PROMPT_DRAFT}\n\n{PRESENTER_VOICE}"
    ctrl_system = f"{MULDRO_SOUL_CORE}\n\n--- YOUR ROLE ---\n{LEAD_PROMPT_CONTROL}\n\n{PRESENTER_VOICE}"

    print("=" * 78)
    print("SPIKE: pure-write terminal-message reliability (REAL sonnet, deepagents loop)")
    print("=" * 78)

    main_failures: list[str] = []
    total_main = 0

    for label, user_message, write_tool in SCENARIOS:
        agent = _build_lead(main_system)
        replies = 0
        writes = 0
        for i in range(N_RUNS):
            total_main += 1
            result = await _run_one(agent, user_message, i)
            reply = _final_reply_text(result)
            called = _tool_was_called(result, write_tool)
            writes += int(called)
            has_reply = bool(reply)
            replies += int(has_reply)
            status = "OK " if (has_reply and called) else "BAD"
            if not has_reply:
                main_failures.append(f"{label} run{i}: NO terminal reply (write_called={called})")
            preview = reply.replace("\n", " ")[:90]
            print(f"  [{status}] {label} run{i}: write={called} reply={has_reply!r:>5} :: {preview}")
        print(
            f"  → {label}: terminal-reply {replies}/{N_RUNS}, write-called {writes}/{N_RUNS}\n"
        )

    # Control: does the model confirm WITHOUT the always-reply rule? (one run per scenario)
    print("-" * 78)
    print("CONTROL (always-reply rule REMOVED — measures load-bearingness, does not gate):")
    ctrl_replies = 0
    for label, user_message, write_tool in SCENARIOS:
        agent = _build_lead(ctrl_system)
        result = await _run_one(agent, user_message, 99)
        reply = _final_reply_text(result)
        ctrl_replies += int(bool(reply))
        preview = reply.replace("\n", " ")[:90]
        print(f"  {label}: reply={bool(reply)!r:>5} :: {preview}")
    print()

    print("=" * 78)
    print(f"MAIN condition (LEAD_PROMPT + PRESENTER_VOICE): "
          f"{total_main - len(main_failures)}/{total_main} turns ended with a terminal reply")
    print(f"CONTROL condition (rule removed): {ctrl_replies}/{len(SCENARIOS)} ended with a reply")
    if main_failures:
        print("\nVERDICT: REFUTED — the terminal-message rule is NOT reliable. Failures:")
        for f in main_failures:
            print(f"  - {f}")
        print("\n⇒ 5a needs a FALLBACK: a forced synthesis turn after the last tool call.")
        return 1
    print("\nVERDICT: CONFIRMED — the lead reliably emits a terminal user-facing reply after a "
          "pure write. The A-5 single-lead reply model is safe to build.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
