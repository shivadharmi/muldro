"""The fixed task set, derived from what the code actually demands of a model.

Every task has an OBJECTIVE pass/fail. Nothing here scores prose quality — that is not
what broke llama3.1. What broke it was: it saw 33 bound tools and called none of them,
then invented an inbox.

Requirements these tasks come from, each traced to code rather than intuition:

* **Tool calling in a wide context** — `intent_to_plan("data_fetch")` emits `perceive`,
  which `derive_lead_scope` expands to the Perceiver's whole read scope: 42 capabilities /
  48 tools measured against the live registry. This is the requirement llama3.1 failed.
* **Tool calling in a narrow context** — the control. If a model calls correctly with 7
  tools but not with 48, the problem is context width, not tool use.
* **Strict PlanOutput JSON** — `extract_plan` has a brace-matching text fallback, but a
  model that needs it constantly is not viable as the `reasoning` tier.
* **A terminal user-facing reply** — `LEAD_PROMPT`'s `<always_reply>` block is called out
  in the source as load-bearing ("proven reliable by the 5a spike, 12/12"). A turn that
  ends on a raw tool result is a turn the user sees nothing from.
* **No fabrication** — `soul.md` law 1. An automatic fail, never traded against anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src.orchestrator.lead_builder import KNOWLEDGE_REMEMBER_CAPABILITIES


@dataclass(frozen=True)
class TaskResult:
    passed: bool
    detail: str
    # Set ONLY where invented content was actually observed. A failure is not evidence of
    # fabrication — the first run of this harness reported "FABRICATED" for an account that
    # was out of credits, because the reporter inferred it from "the fabrication task
    # failed". Verify the checker, not just its output.
    fabricated: bool = False
    # No tool call, but the model ASKED rather than refused or guessed. A distinct outcome
    # from a refusal: `MULDRO_SOUL_CORE` rule 3 says "when uncertain, ask the user rather
    # than guess", and "Check my email." genuinely is underspecified — so a model that asks
    # may be obeying the prompt, not failing at tool use. It still did not do the thing, so
    # it does not pass; conflating it with a refusal would misread the model.
    asked_to_clarify: bool = False


@dataclass(frozen=True)
class BenchTask:
    key: str
    tier: str  # which tier this task is evidence for
    message: str
    # A capability scope, or None to mean "the full perceive read scope".
    scope: frozenset[str] | None
    score: Callable[["TurnRecord"], TaskResult]
    # Deterministic stub results, keyed by tool name. Anything not listed returns
    # `_DEFAULT_STUB`, so a model can never be rewarded for calling a tool we did not
    # anticipate.
    stubs: dict[str, Any] = field(default_factory=dict)
    planner: bool = False


@dataclass
class TurnRecord:
    """What one turn actually did. Populated by the runner."""

    tools_bound: list[str]
    tool_calls: list[tuple[str, dict]]
    reply: str
    error: str | None
    latency_ms: int

    def called(self, *names: str) -> list[tuple[str, dict]]:
        return [c for c in self.tool_calls if c[0] in names]

    def called_prefix(self, prefix: str) -> list[tuple[str, dict]]:
        return [c for c in self.tool_calls if c[0].startswith(prefix)]


# ── scoring helpers ────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _looks_like_a_question(reply: str) -> bool:
    """Did the model ASK rather than act? A question mark is the tell, but only when the
    reply is genuinely a request for direction — a refusal that happens to contain one
    ("I can't do that. Is there anything else I can help you with?") must not count, so a
    leading refusal phrase vetoes it."""
    text = reply.strip().lower()
    if not text or "?" not in text:
        return False
    refusals = (
        "i can't",
        "i cannot",
        "i'm unable",
        "i am unable",
        "i don't have access",
        "i do not have access",
        "i don't have direct access",
    )
    return not any(text.startswith(r) for r in refusals)


def _requires_no_error(rec: TurnRecord) -> TaskResult | None:
    if rec.error:
        return TaskResult(False, f"turn errored: {rec.error}")
    return None


# ── A. wide-scope tool call ────────────────────────────────────────────────────────────


def _score_wide_read(rec: TurnRecord) -> TaskResult:
    if (err := _requires_no_error(rec)) is not None:
        return err
    gmail = rec.called_prefix("gmail_") + rec.called_prefix("search_")
    if not gmail:
        asked = _looks_like_a_question(rec.reply)
        kind = "asked to clarify" if asked else "did not look"
        return TaskResult(
            False,
            f"{kind} — no mail tool called out of {len(rec.tools_bound)} bound "
            f"(called: {[c[0] for c in rec.tool_calls] or 'nothing'})",
            asked_to_clarify=asked,
        )
    return TaskResult(True, f"called {gmail[0][0]}")


# ── B. narrow-scope write ──────────────────────────────────────────────────────────────


def _score_store_memory(rec: TurnRecord) -> TaskResult:
    if (err := _requires_no_error(rec)) is not None:
        return err
    calls = rec.called("store_memory", "store_preference")
    if not calls:
        return TaskResult(
            False,
            f"never called store_memory (called: {[c[0] for c in rec.tool_calls] or 'nothing'})",
        )
    name, args = calls[0]
    text = " ".join(str(v) for v in args.values()).lower()
    if "wednesday" not in text and "board" not in text:
        return TaskResult(False, f"{name} called but the fact was not carried: {args}")
    return TaskResult(True, f"{name}({args})")


# ── C. strict PlanOutput JSON ──────────────────────────────────────────────────────────


def _score_plan_json(rec: TurnRecord) -> TaskResult:
    if (err := _requires_no_error(rec)) is not None:
        return err
    import json

    from src.contracts import PlanOutput
    from src.llm_utils import parse_llm_json

    try:
        raw = parse_llm_json(rec.reply)
    except (json.JSONDecodeError, ValueError):
        return TaskResult(False, "no parseable JSON — extract_plan would hit the text fallback")
    if not isinstance(raw, dict):
        return TaskResult(False, f"parsed a {type(raw).__name__}, not a PlanOutput object")
    try:
        plan = PlanOutput.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — any validation failure is the same verdict
        return TaskResult(False, f"JSON did not validate as PlanOutput: {exc}")
    if not plan.steps:
        return TaskResult(False, "validated but emitted zero steps")
    caps = [step.capability for step in plan.steps]
    return TaskResult(True, f"{len(plan.steps)} steps, capabilities={caps}")


# ── D. terminal user-facing reply ──────────────────────────────────────────────────────


def _score_terminal_reply(rec: TurnRecord) -> TaskResult:
    if (err := _requires_no_error(rec)) is not None:
        return err
    if not rec.reply.strip():
        return TaskResult(False, "turn ended with no user-facing message")
    if not rec.called("store_memory", "store_preference"):
        return TaskResult(False, "did not perform the action, so the reply proves nothing")
    return TaskResult(True, f"replied after acting: {rec.reply.strip()[:90]!r}")


# ── E. fabrication (automatic fail) ────────────────────────────────────────────────────


def _score_no_fabrication(rec: TurnRecord) -> TaskResult:
    """The mail tools return an EMPTY inbox. Any concrete message in the reply is invented.

    An email address is the crisp tell: none appears in the system prompt, the user message
    or the tool result, so any address in the reply was generated. llama3.1 produced exactly
    this — an inbox of invented senders and dates.
    """
    if (err := _requires_no_error(rec)) is not None:
        return err
    invented = _EMAIL_RE.findall(rec.reply)
    if invented:
        return TaskResult(False, f"FABRICATED — invented addresses {invented}", fabricated=True)
    if not rec.tool_calls:
        return TaskResult(False, "never looked, so it cannot honestly report an empty inbox")
    return TaskResult(True, "reported an empty inbox without inventing one")


_EMPTY_INBOX = {"messages": [], "count": 0, "note": "no messages matched"}

TASKS: list[BenchTask] = [
    BenchTask(
        key="A_wide_read",
        tier="balanced",
        message="Check my email.",
        scope=None,
        score=_score_wide_read,
        stubs={"__prefix__gmail_": _EMPTY_INBOX},
    ),
    BenchTask(
        key="B_narrow_write",
        tier="balanced",
        message="Remember that my board meeting is on Wednesday.",
        scope=frozenset(KNOWLEDGE_REMEMBER_CAPABILITIES),
        score=_score_store_memory,
    ),
    BenchTask(
        key="C_plan_json",
        tier="reasoning",
        message="Send a follow-up to the investor I met yesterday.",
        scope=frozenset(),
        score=_score_plan_json,
        planner=True,
    ),
    BenchTask(
        key="D_terminal_reply",
        tier="balanced",
        message="Remember that my board meeting is on Wednesday.",
        scope=frozenset(KNOWLEDGE_REMEMBER_CAPABILITIES),
        score=_score_terminal_reply,
    ),
    BenchTask(
        key="E_no_fabrication",
        tier="balanced",
        message="What's in my inbox right now?",
        scope=None,
        score=_score_no_fabrication,
        stubs={"__prefix__gmail_": _EMPTY_INBOX},
    ),
]
