"""Score candidate models against the fixed task set.

    uv run python -m scripts.model_bench --list
    uv run python -m scripts.model_bench --candidate openai/gpt-5-mini
    uv run python -m scripts.model_bench --candidate ollama-cloud/gpt-oss:120b --trials 3

Needs the infra up (`docker compose up -d`) — tool schemas come from the live registry.
No external service is called: every tool result is a fixed stub. Reports per TIER, not
overall, because the three tiers ask different things of a model and need not share one.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

from .candidates import Candidate, registry
from .runner import run_task
from .tasks import TASKS

# Keys live in backend/.env (gitignored), the same file pydantic-settings reads. Load it so
# a candidate can find its key without the operator exporting anything by hand.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


def _build_tool_executor():
    from src.config.settings import get_settings
    from src.models.database import get_session_factory
    from src.orchestrator.event_publisher import EventPublisher
    from src.orchestrator.tool_executor import ToolExecutor

    settings = get_settings()

    def _provider():
        return get_session_factory()

    return ToolExecutor(EventPublisher(settings, None, _provider), _provider)


async def _workspace_id() -> str:
    from sqlalchemy import select

    from src.models.database import get_session_factory
    from src.models.users import Workspace

    async with get_session_factory()() as db:
        ws = (await db.execute(select(Workspace).limit(1))).scalar_one_or_none()
        if ws is None:
            raise SystemExit("no workspace rows — seed one first (scripts/create_test_user.py)")
        return ws.workspace_id


async def _run_candidate(cand: Candidate, trials: int) -> dict:
    from src.models.database import get_session_factory

    try:
        model = cand.build()
    except Exception as exc:  # noqa: BLE001
        print(f"  !! cannot build {cand.label}: {exc}")
        return {}

    workspace_id = await _workspace_id()
    executor = _build_tool_executor()
    db_factory = get_session_factory()
    results: dict[str, list] = defaultdict(list)

    for task in TASKS:
        for trial in range(trials):
            rec = await run_task(
                task,
                model,
                tool_executor=executor,
                db_factory=db_factory,
                workspace_id=workspace_id,
                user_id="usr_bench",
                supports_prompt_cache=cand.supports_prompt_cache,
            )
            verdict = task.score(rec)
            results[task.key].append((verdict, rec))
            mark = "PASS" if verdict.passed else "FAIL"
            trial_label = f" [{trial + 1}/{trials}]" if trials > 1 else ""
            print(
                f"  {mark}  {task.key:<18}{trial_label} "
                f"({len(rec.tools_bound)} tools bound, {rec.latency_ms}ms) — {verdict.detail}"
            )
            if not verdict.passed and not rec.error:
                # The reply distinguishes the failure modes that matter: a model that
                # REFUSED ("I can't do that") is a different problem from one that
                # answered helpfully without ever looking.
                snippet = " ".join(rec.reply.split())[:160] or "<empty reply>"
                print(f"        reply: {snippet!r}")
    return results


def _report(label: str, results: dict) -> None:
    if not results:
        return
    by_tier: dict[str, list[bool]] = defaultdict(list)
    fabricated = False
    asked = 0
    for task in TASKS:
        for verdict, _rec in results.get(task.key, []):
            by_tier[task.tier].append(verdict.passed)
            if verdict.fabricated:
                fabricated = True
            if verdict.asked_to_clarify:
                asked += 1
    print(f"\n  == {label} ==")
    for tier, outcomes in sorted(by_tier.items()):
        print(f"     {tier:<10} {sum(outcomes)}/{len(outcomes)} passed")
    if asked:
        print(f"     ({asked} non-pass turn(s) ASKED the user rather than refusing)")
    if fabricated:
        print("     ** FABRICATED a result — automatic fail, not tradeable **")


async def main() -> None:
    ap = argparse.ArgumentParser(prog="model_bench")
    ap.add_argument("--candidate", action="append", help="label; repeatable. Default: all")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    all_candidates = registry()
    if args.list:
        for c in all_candidates:
            print(f"  {c.label:<32} {c.note}")
        return

    chosen = all_candidates
    if args.candidate:
        wanted = set(args.candidate)
        chosen = [c for c in all_candidates if c.label in wanted]
        missing = wanted - {c.label for c in chosen}
        if missing:
            raise SystemExit(f"unknown candidate(s): {sorted(missing)}")

    for cand in chosen:
        print(f"\n=== {cand.label} ===")
        _report(cand.label, await _run_candidate(cand, args.trials))


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    asyncio.run(main())
