"""Units for the rows muldro wrote itself: runs, briefings, the review queue.

`stored_units.py` covers the world outside; this covers muldro's own. Every
frame here is built through `frame_for_row`, so a model-authored plan goal or
briefing headline is neutralized rather than raising inside Frame's validator.

`run` and `alert` used to be two surface kinds over one table
(`surface_builder._build_run_surfaces` / `_build_alert_surfaces`). FrameKind
has one `run` and FrameStatus carries the difference, because you cannot rank
things that do not look alike (spec §4.1).

Every function here is TOTAL. A family that cannot be read returns nothing and
logs; it never costs the feed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep
from src.view.contracts import FrameStatus, Unit
from src.view.frame import frame_for_row

logger = logging.getLogger(__name__)

__all__ = ["FAILED_RUN_WINDOW_HOURS", "MAX_RUN_UNITS", "run_headline", "run_unit", "run_units"]

# A failed run is worth the founder's attention for a day; after that it is
# history, and `/history` is where history lives.
FAILED_RUN_WINDOW_HOURS = 24
MAX_RUN_UNITS = 20

# TaskRun.status -> FrameStatus. Exhaustive over the statuses the query below
# selects; anything else is a bug in the query, not in the map.
_RUN_STATUS: dict[str, FrameStatus] = {
    "running": "running",
    "paused": "needs_you",
    "awaiting_approval": "needs_you",
    "awaiting_input": "needs_you",
    "blocked": "needs_you",
    "failed": "failed",
}

_ACTIVE = ("running", "paused", "awaiting_approval", "awaiting_input", "blocked")


def run_headline(*, plan_goal: str | None, step_name: str | None) -> str:
    """What this run is FOR, or "" when muldro cannot say.

    TaskRun carries no goal of its own — it points at a Plan, and the Plan has
    one. The old builder never looked: it took the first step's name and fell
    back to the literal "Run", so every unnamed run read alike. That is defect
    1 in miniature, and "" is the honest answer, because `frame_for_row` turns
    it into `muldro run` — which at least names what muldro knows.
    """
    for candidate in (plan_goal, step_name):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def run_unit(run: Any, *, headline: str) -> Unit | None:
    """Shape one TaskRun row into a Unit. Pure. None when the row is unusable.

    A run carries no Quote. Its error message is muldro's own prose, and a
    Quote is external text shown under a named human's name — quoting muldro
    there is the same misattribution in the other direction.
    """
    try:
        frame = frame_for_row(
            source="muldro",
            entity_type="run",
            entity_id=run.run_id,
            kind="run",
            status=_RUN_STATUS.get(getattr(run, "status", ""), "running"),
            headline=headline,
            occurred_at=getattr(run, "started_at", None) or getattr(run, "created_at", None),
            updated_at=getattr(run, "completed_at", None) or getattr(run, "updated_at", None),
        )
    except Exception as exc:  # noqa: BLE001 - one bad row costs its own card
        logger.warning("feed_run_unit_failed run=%r error=%s", getattr(run, "run_id", "?"), exc)
        return None
    return Unit(frame=frame, body="", quotes=())


async def _first_step_names(db: Any, run_ids: list[str]) -> dict[str, str]:
    """`{run_id: first step name}` for the runs that need a fallback headline.

    One batched query, never one per run. A failure here costs headlines, not
    cards — `run_headline` then returns "" and `frame_for_row` names what
    muldro knows.
    """
    if not run_ids:
        return {}
    try:
        result = await db.execute(
            select(TaskStep)
            .where(TaskStep.run_id.in_(run_ids), TaskStep.name.isnot(None))
            .order_by(TaskStep.run_id, TaskStep.step_order)
        )
        names: dict[str, str] = {}
        for step in result.scalars().all():
            names.setdefault(step.run_id, step.name)
        return names
    except Exception as exc:  # noqa: BLE001 - a headline is not a card
        logger.warning("feed_run_step_names_failed error=%s", exc)
        return {}


async def run_units(db: Any, *, workspace_id: str, now: datetime) -> list[Unit]:
    """One `run` Unit per active run, plus each run that failed recently."""
    cutoff = now - timedelta(hours=FAILED_RUN_WINDOW_HOURS)
    try:
        result = await db.execute(
            select(TaskRun, Plan.goal)
            .outerjoin(Plan, TaskRun.plan_id == Plan.plan_id)
            .where(
                TaskRun.workspace_id == workspace_id,
                TaskRun.source != "user_message",
                (TaskRun.status.in_(_ACTIVE))
                | ((TaskRun.status == "failed") & (TaskRun.updated_at >= cutoff)),
            )
            .order_by(TaskRun.updated_at.desc())
            .limit(MAX_RUN_UNITS)
        )
        rows = list(result.all())
    except Exception as exc:  # noqa: BLE001 - one family must not cost the feed
        logger.warning("feed_run_read_failed workspace=%s error=%s", workspace_id, exc)
        return []

    needs_fallback = [run.run_id for run, goal in rows if not (goal or "").strip()]
    step_names = await _first_step_names(db, needs_fallback)

    units: list[Unit] = []
    for run, goal in rows:
        unit = run_unit(
            run,
            headline=run_headline(plan_goal=goal, step_name=step_names.get(run.run_id)),
        )
        if unit is not None:
            units.append(unit)
    return units
