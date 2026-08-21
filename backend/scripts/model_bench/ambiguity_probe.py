"""Is "it asked instead of acting" the MODEL's ceiling, or the PROMPT's doing?

gpt-5-mini asked the user on 9 of 9 turns where it could have acted — never refusing,
never fabricating. That is exactly what `MULDRO_SOUL_CORE` rule 3 asks for ("when uncertain,
ask the user rather than guess"), and the benchmark's own messages ARE underspecified:
"Check my email." does not say which messages, and "my board meeting is on Wednesday" does
not say which Wednesday.

So the score alone cannot separate two very different findings:

  (a) the model cannot reliably drive tool calls — a ceiling, and disqualifying; versus
  (b) the model obeys the prompt harder than the others — a prompt-tuning problem.

This probe re-runs the same two act-tasks with the ambiguity removed. A model that acts
here and asks on the standard task is case (b). One that asks here too is case (a).

Deliberately NOT part of `TASKS`: the scored set has to stay stable for candidates already
measured against it.

    uv run python -m scripts.model_bench.ambiguity_probe --candidate openai/gpt-5-mini
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv

from src.orchestrator.lead_builder import KNOWLEDGE_REMEMBER_CAPABILITIES

from .candidates import registry
from .runner import run_task
from .tasks import BenchTask, _score_store_memory, _score_wide_read

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

_EMPTY_INBOX = {"messages": [], "count": 0, "note": "no messages matched"}

# Same scope, same scorer, same stubs as A_wide_read / B_narrow_write. ONLY the message
# changes — every ambiguity a model could reasonably ask about is answered up front.
PROBES = [
    BenchTask(
        key="A_explicit",
        tier="balanced",
        message=(
            "Fetch the 10 most recent messages from my Gmail inbox now and summarise them. "
            "Do not ask me anything first — use these defaults."
        ),
        scope=None,
        score=_score_wide_read,
        stubs={"__prefix__gmail_": _EMPTY_INBOX},
    ),
    BenchTask(
        key="B_explicit",
        tier="balanced",
        message=(
            "Store this exact fact in my memory now, without asking me anything first: "
            "'Board meeting on Wednesday 26 August 2026 at 10:00 Europe/London.'"
        ),
        scope=frozenset(KNOWLEDGE_REMEMBER_CAPABILITIES),
        score=_score_store_memory,
    ),
]


async def main() -> None:
    ap = argparse.ArgumentParser(prog="ambiguity_probe")
    ap.add_argument("--candidate", action="append", required=True)
    ap.add_argument("--trials", type=int, default=3)
    args = ap.parse_args()

    from sqlalchemy import select

    from src.config.settings import get_settings
    from src.models.database import get_session_factory
    from src.models.users import Workspace
    from src.orchestrator.event_publisher import EventPublisher
    from src.orchestrator.tool_executor import ToolExecutor

    def _provider():
        return get_session_factory()

    executor = ToolExecutor(EventPublisher(get_settings(), None, _provider), _provider)
    db_factory = get_session_factory()
    async with db_factory() as db:
        workspace_id = (await db.execute(select(Workspace).limit(1))).scalar_one().workspace_id

    wanted = set(args.candidate)
    for cand in [c for c in registry() if c.label in wanted]:
        print(f"\n=== {cand.label} ===")
        try:
            model = cand.build()
        except Exception as exc:  # noqa: BLE001
            print(f"  !! cannot build: {exc}")
            continue
        for probe in PROBES:
            acted = 0
            ran = 0
            for trial in range(args.trials):
                rec = await run_task(
                    probe,
                    model,
                    tool_executor=executor,
                    db_factory=db_factory,
                    workspace_id=workspace_id,
                    user_id="usr_bench",
                    supports_prompt_cache=cand.supports_prompt_cache,
                )
                if rec.error:
                    print(f"  ERR   {probe.key} [{trial + 1}] — {rec.error[:110]}")
                    continue
                ran += 1
                verdict = probe.score(rec)
                acted += verdict.passed
                mark = "PASS" if verdict.passed else "FAIL"
                print(
                    f"  {mark}  {probe.key} [{trial + 1}] ({rec.latency_ms}ms) — {verdict.detail}"
                )
                if not verdict.passed:
                    print(f"        reply: {' '.join(rec.reply.split())[:150]!r}")
            if ran:
                print(f"  -> {probe.key}: acted {acted}/{ran} when told explicitly")


if __name__ == "__main__":
    asyncio.run(main())
