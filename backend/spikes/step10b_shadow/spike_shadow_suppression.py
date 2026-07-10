"""Step 10B Phase 0 de-risking spike — shadow write-suppression.

THROWAWAY exploratory code. Not imported by src/, not wired into pytest.
Purpose: falsify (or confirm) the 3 claims the "shadow-compare" cutover-control-plane
design depends on, BEFORE building the real ShadowToolExecutor + shadow harness.

Design under test (see docs/superpowers/plans/... step 10B): Step 10B runs the
NON-authoritative deep agent runtime alongside the authoritative legacy runtime,
diffing their READ-ONLY decision outputs, WITHOUT ever executing a real write.
The mechanism is a ShadowToolExecutor: READ-capability tool calls pass through to
the real executor; WRITE-capability tool calls are HARD-SUPPRESSED, returning a
synthetic result `{"shadow_suppressed": True, "tool": name, "capability": cap}`
that NEVER reaches real dispatch.

Claims under test
------------------
(a) SUPPRESSION: a write-capability tool call under ShadowToolExecutor returns the
    synthetic suppressed shape and the real-dispatch spy is called ZERO times for
    that tool. The read tool call, by contrast, DOES reach the spy (passthrough).

(b) CONTINUATION (the DISPROVE-able claim): suppressing a write mid-agent-loop does
    NOT derail the loop. A minimal but realistic agent loop — read tool, then write
    tool (suppressed), then final answer — must thread the synthetic suppressed
    result back into message history as a well-formed tool-result message (mirrors
    src/orchestrator/agent_loop.py's Anthropic-shaped
    {"type": "tool_result", "tool_use_id": ..., "content": json.dumps(result)}
    convention) and the fake model's FINAL step must actually parse and branch on
    that threaded-back content before producing its answer. This is made
    non-tautological by making the assertion depend on the *content* of what was
    threaded back (see NON-TAUTOLOGICAL NOTE below).

(c) CAPTURABILITY: the loop's route + tool-intent set + final text can be captured
    into a ShadowDecision-shaped dataclass suitable for future diffing.

NON-TAUTOLOGICAL NOTE (claim b)
--------------------------------
A fully scripted fake model that ignores tool results would always reach a final
answer, proving nothing. To avoid that, FakeModel._compose_final_answer() does NOT
just count turns — it iterates the actual message history, json.loads()'s each
threaded-back tool_result's `content` string, and inspects the parsed dict's keys
to decide what happened to the write (`shadow_suppressed` present -> suppressed
branch; `message_id` present -> real-write branch; neither -> no-write branch).
The MAIN run's final assertion checks that the produced text lands specifically in
the *suppressed* branch (`"suppressed" in final_text`). If the synthetic dict's
shape didn't survive json.dumps/json.loads round-tripping, or a KeyError/TypeError
were raised while parsing it, the loop would crash or produce the wrong branch and
the assertion would FAIL -- this is the falsifiable surface.

Run
---
    cd backend
    uv run python spikes/step10b_shadow/spike_shadow_suppression.py
    uv run python spikes/step10b_shadow/spike_shadow_suppression.py --negative-control-only

Expected (default invocation, no flags): runs the MAIN claim run (a/b/c all PASS,
"VERDICT: PROVEN"), then automatically also runs the NEGATIVE CONTROL (which must
FAIL claim (a) on purpose, proving suppression is load-bearing and not incidental).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── sys.path bootstrap ───────────────────────────────────────────────────────
# Make `import src....` work regardless of cwd (repo root or backend/), by
# inserting the `backend/` directory (two levels up from this file) into
# sys.path. This is the ONLY coupling to the real repo: we import the real,
# pure, dependency-free classifier so the spike reflects real behavior instead
# of reimplementing it.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from src.integrations.capabilities import is_read_only_capability  # noqa: E402

# ── fake tool <-> capability mapping (spike-local; real mapping lives in
#    src/tools/catalog.py, not reimplemented here) ───────────────────────────
TOOL_CAPABILITY: dict[str, str] = {
    "gmail_search": "email.search",  # read
    "gmail_send": "email.send",  # write
}


def resolve_capability(tool_name: str) -> str | None:
    """Fake tool_name -> capability resolver (real one: ToolRegistry.get_tool)."""
    return TOOL_CAPABILITY.get(tool_name)


# ── real-dispatch spy (mirrors ToolExecutor.execute_tool signature) ─────────
@dataclass
class RealDispatchSpy:
    """Stand-in for src.orchestrator.tool_executor.ToolExecutor.execute_tool.

    Records every call it receives so the spike can assert the write NEVER
    reaches it under ShadowToolExecutor (claim a), and DOES reach it once the
    negative control disables suppression.
    """

    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        self.calls.append((tool_name, tool_input))
        if tool_name == "gmail_search":
            return {
                "status": "ok",
                "results": [
                    {"id": "m1", "subject": "Invoice #123"},
                    {"id": "m2", "subject": "Invoice #124"},
                ],
            }
        if tool_name == "gmail_send":
            # Only ever reached if suppression is OFF (negative control).
            return {"status": "ok", "message_id": "sent_999"}
        return {"status": "ok", "result": None}

    def count(self, tool_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == tool_name)


# ── prototype ShadowToolExecutor under test ──────────────────────────────────
class ShadowToolExecutor:
    """Throwaway prototype of the Step 10B shadow executor.

    Mirrors the real execute_tool signature (tool_name, tool_input, user_id,
    workspace_id="") -> dict. WRITE-capability calls are hard-suppressed:
    they return a synthetic result and NEVER reach `real_dispatch`. READ-capability
    calls pass through untouched.

    `force_classify_as_read` is the negative-control knob: when True, every
    capability (including writes) is (wrongly) treated as read-only, so the write
    passes through to the real dispatch. This is used ONLY to prove claim (a) is
    load-bearing -- it must never be set True in the real implementation.
    """

    def __init__(
        self,
        real_dispatch: RealDispatchSpy,
        resolve_capability_fn=resolve_capability,
        *,
        force_classify_as_read: bool = False,
    ) -> None:
        self._real = real_dispatch
        self._resolve_capability = resolve_capability_fn
        self._force_classify_as_read = force_classify_as_read

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        capability = self._resolve_capability(tool_name)

        if self._force_classify_as_read:
            # NEGATIVE CONTROL ONLY: deliberately wrong classification.
            is_read = True
        elif capability is None:
            # Unknown capability -> is_read_only_capability("") returns False via
            # its dict.get(...) default -> fail-closed -> treated as WRITE -> suppressed.
            is_read = is_read_only_capability(capability or "")
        else:
            is_read = is_read_only_capability(capability)

        if is_read:
            return await self._real.execute_tool(tool_name, tool_input, user_id, workspace_id)

        # HARD SUPPRESSION: never call self._real here.
        return {
            "shadow_suppressed": True,
            "tool": tool_name,
            "capability": capability,
        }


# ── minimal but realistic agent loop ─────────────────────────────────────────
@dataclass(frozen=True)
class ModelStep:
    kind: str  # "tool_call" | "final"
    tool_name: str | None = None
    tool_input: dict | None = None
    call_id: str | None = None
    text: str | None = None


class FakeModel:
    """Scripted fake model: emits a fixed tool sequence, then a FINAL answer that
    is computed by actually reading the accumulated message history (see
    NON-TAUTOLOGICAL NOTE at module top) -- not a hardcoded string.
    """

    def __init__(self, script: list[tuple[str, dict]]) -> None:
        self._script = script
        self._step_index = 0

    def next(self, messages: list[dict]) -> ModelStep:
        if self._step_index < len(self._script):
            tool_name, tool_input = self._script[self._step_index]
            call_id = f"call_{self._step_index}"
            self._step_index += 1
            return ModelStep(
                kind="tool_call", tool_name=tool_name, tool_input=tool_input, call_id=call_id
            )
        return ModelStep(kind="final", text=self._compose_final_answer(messages))

    @staticmethod
    def _compose_final_answer(messages: list[dict]) -> str:
        """Reads the REAL threaded-back tool_result content (not a no-op). This is
        the crux of claim (b): if the synthetic suppressed result didn't survive
        json.dumps/json.loads round-tripping through the message history, this
        would raise or branch wrong, and the caller's assertion on the returned
        text would fail.
        """
        read_summary: dict | None = None
        write_result: dict | None = None

        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                # This json.loads is the real threading test: the content string
                # is exactly what a real Anthropic-shaped tool_result carries
                # (see agent_loop.py: json.dumps(result) as `content`).
                parsed = json.loads(block["content"])
                if "results" in parsed:
                    read_summary = parsed
                elif "shadow_suppressed" in parsed or "message_id" in parsed:
                    write_result = parsed

        n_found = len(read_summary["results"]) if read_summary else 0

        if write_result is not None and write_result.get("shadow_suppressed"):
            write_desc = (
                f"write suppressed by shadow harness "
                f"(tool={write_result['tool']}, capability={write_result['capability']})"
            )
        elif write_result is not None:
            write_desc = f"write executed for real (message_id={write_result.get('message_id')})"
        else:
            write_desc = "no write was attempted"

        return f"Found {n_found} matching email(s). {write_desc}."


@dataclass(frozen=True)
class ShadowDecision:
    """The captured decision shape Phase 2's real diff harness will compare
    legacy-vs-deep on. Deliberately minimal for the spike."""

    route: str
    tool_intents: frozenset[str]
    final_text: str


def _derive_route(tool_intents: frozenset[str]) -> str:
    """Toy stand-in for CapabilityResolver: any write-capability intent routes to
    'executor', pure-read intents route to 'perceiver'. Not the real routing
    logic -- just enough to prove a route field is capturable (claim c)."""
    for tool_name in tool_intents:
        cap = resolve_capability(tool_name)
        if cap is not None and not is_read_only_capability(cap):
            return "executor"
    return "perceiver"


async def run_agent_loop(
    model: FakeModel, executor: ShadowToolExecutor, user_id: str, workspace_id: str
) -> ShadowDecision:
    """Minimal but realistic loop: model emits tool calls or a final answer; tool
    calls are dispatched through the executor and threaded back into `messages`
    as Anthropic-shaped tool_result blocks (mirrors agent_loop.py), exactly like
    a real agent runtime would, before the model is asked for its next step.
    """
    messages: list[dict] = [
        {
            "role": "user",
            "content": "Search my email for invoices, then send a follow-up to the vendor.",
        }
    ]
    tool_intents: set[str] = set()

    while True:
        step = model.next(messages)

        if step.kind == "final":
            return ShadowDecision(
                route=_derive_route(frozenset(tool_intents)),
                tool_intents=frozenset(tool_intents),
                final_text=step.text or "",
            )

        assert step.kind == "tool_call"
        tool_intents.add(step.tool_name)

        result = await executor.execute_tool(
            step.tool_name, step.tool_input or {}, user_id, workspace_id
        )

        # Assistant tool_use turn (mirrors real Anthropic message shape).
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": step.call_id,
                        "name": step.tool_name,
                        "input": step.tool_input,
                    }
                ],
            }
        )

        # Tool-result turn threaded back as role="user" content, exactly like
        # agent_loop.py's `messages.append({"role": "user", "content": tool_results})`.
        is_error = (
            isinstance(result, dict)
            and "error" in result
            and result.get("status") not in ("ok", "success", "updated", "ingested")
        )
        result_content = json.dumps(result) if isinstance(result, dict) else str(result)
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": step.call_id,
                        "content": result_content,
                        **({"is_error": True} if is_error else {}),
                    }
                ],
            }
        )


# ── claim runners ─────────────────────────────────────────────────────────────
SCRIPT: list[tuple[str, dict]] = [
    ("gmail_search", {"query": "invoice"}),
    ("gmail_send", {"to": "vendor@example.com", "subject": "Follow-up", "body": "..."}),
]


async def run_main_claims() -> bool:
    print("=" * 78)
    print("MAIN RUN — ShadowToolExecutor with REAL classification (suppression ON)")
    print("=" * 78)

    spy = RealDispatchSpy()
    executor = ShadowToolExecutor(spy, resolve_capability)
    model = FakeModel(list(SCRIPT))

    decision = await run_agent_loop(model, executor, user_id="u1", workspace_id="w1")

    # ── claim (a): write never reaches real dispatch; read does ──
    write_calls = spy.count("gmail_send")
    read_calls = spy.count("gmail_search")
    claim_a = write_calls == 0 and read_calls == 1
    print(
        f"[a] suppression: gmail_send real-dispatch calls={write_calls} (want 0), "
        f"gmail_search real-dispatch calls={read_calls} (want 1) "
        f"-> {'PASS' if claim_a else 'FAIL'}"
    )

    # ── claim (b): loop reached a final answer AND it demonstrably read the
    #     threaded-back synthetic result (non-tautological: text must land in the
    #     'suppressed' branch, not the 'no write attempted' / 'real write' branch) ──
    reached_final = bool(decision.final_text)
    read_the_suppression = "suppressed by shadow harness" in decision.final_text
    claim_b = reached_final and read_the_suppression
    print(f"[b] continuation: final_text={decision.final_text!r}")
    print(
        f"[b] continuation: reached_final={reached_final}, "
        f"read_threaded_suppression={read_the_suppression} -> {'PASS' if claim_b else 'FAIL'}"
    )

    # ── claim (c): decision is captured with non-empty route + tool-intents + text ──
    claim_c = bool(decision.route) and bool(decision.tool_intents) and bool(decision.final_text)
    print(
        f"[c] capturable: route={decision.route!r}, "
        f"tool_intents={sorted(decision.tool_intents)}, "
        f"final_text_len={len(decision.final_text)} -> {'PASS' if claim_c else 'FAIL'}"
    )

    all_pass = claim_a and claim_b and claim_c
    print(f"\nMAIN RUN VERDICT: {'PROVEN' if all_pass else 'DISPROVEN'}")
    return all_pass


async def run_negative_control() -> bool:
    print()
    print("=" * 78)
    print("NEGATIVE CONTROL — write-classification forced to 'read' (suppression OFF)")
    print("=" * 78)

    spy = RealDispatchSpy()
    executor = ShadowToolExecutor(spy, resolve_capability, force_classify_as_read=True)
    model = FakeModel(list(SCRIPT))

    decision = await run_agent_loop(model, executor, user_id="u1", workspace_id="w1")

    write_calls = spy.count("gmail_send")
    claim_a_should_fail = write_calls == 1
    verdict_str = (
        "CORRECTLY FAILED claim (a)" if claim_a_should_fail else "UNEXPECTED PASS — no teeth!"
    )
    print(
        f"[a] (expected to FAIL here) gmail_send real-dispatch calls={write_calls} "
        f"(want 1 to prove suppression is load-bearing) -> {verdict_str}"
    )
    print(f"    (loop still completed: final_text={decision.final_text!r})")

    # The negative control is "successful" (has teeth) iff it demonstrates the
    # write DID reach real dispatch once suppression is disabled.
    return claim_a_should_fail


async def main_async(negative_control_only: bool) -> int:
    if negative_control_only:
        control_has_teeth = await run_negative_control()
        return 0 if control_has_teeth else 1

    main_pass = await run_main_claims()
    control_has_teeth = await run_negative_control()

    print()
    print("=" * 78)
    if main_pass and control_has_teeth:
        print("OVERALL VERDICT: PROVEN")
        print("  - Main run: claims (a) suppression, (b) continuation, (c) capturability all PASS.")
        print(
            "  - Negative control: correctly demonstrates claim (a) FAILS when "
            "classification is wrong -> suppression is load-bearing, not incidental."
        )
    else:
        print("OVERALL VERDICT: DISPROVEN")
        if not main_pass:
            print("  - Main run FAILED one or more claims. See [a]/[b]/[c] above.")
        if not control_has_teeth:
            print(
                "  - Negative control did NOT demonstrate a real difference "
                "(no teeth) -- the main-run PASS may be vacuous."
            )
    print("=" * 78)

    return 0 if (main_pass and control_has_teeth) else 1


if __name__ == "__main__":
    _flags = {"--negative-control-only", "--negative-control"}
    negative_control_only = bool(_flags & set(sys.argv))
    exit_code = asyncio.run(main_async(negative_control_only))
    sys.exit(exit_code)
