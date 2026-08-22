"""Units for the rows muldro wrote itself: runs, briefings, the review queue.

`stored_units.py` covers the world outside; this covers muldro's own. Every
frame here is built through `frame_for_row`, so a model-authored plan goal or
briefing headline is neutralized rather than raising inside Frame's validator.

`run` and `alert` used to be two surface kinds over one table. FrameKind has one
`run` and FrameStatus carries the difference, because a single ranker cannot
order items that do not share a shape.

Every function here is TOTAL. A family that cannot be read returns nothing and
logs; it never costs the feed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.models.memory import Memory
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep
from src.view.contracts import FrameStatus, Unit
from src.view.frame import frame_for_row

logger = logging.getLogger(__name__)

__all__ = [
    "COMPLETED_RUN_WINDOW_HOURS",
    "FAILED_RUN_WINDOW_HOURS",
    "MAX_RUN_UNITS",
    "briefing_units",
    "connector_health_unit",
    "insight_units",
    "prepared_work_unit",
    "run_headline",
    "run_unit",
    "run_units",
]

# A failed run is worth the founder's attention for a day; after that it is
# history, and `/history` is where history lives.
FAILED_RUN_WINDOW_HOURS = 24

# A run that SUCCEEDED still has to be seen once. Only active and failed runs
# used to surface, so autonomous work appeared while it ran and then vanished —
# the workspace could never answer "what did you do overnight", and the only
# record was a briefing generated at 07:00. Shorter than the failed window: a
# success is worth reporting, not chasing. Long enough to span a night, so the
# answer is still there at breakfast.
COMPLETED_RUN_WINDOW_HOURS = 12

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
    "timed_out": "failed",
    "completed": "done",
    # A read-back that CONTRADICTED what the step reported. Not a success and
    # not a clean failure — the one terminal state that genuinely wants a
    # human, which is why it maps to `needs_you` rather than to `done`.
    "partially_completed": "needs_you",
}

_ACTIVE = ("running", "paused", "awaiting_approval", "awaiting_input", "blocked")

# Terminal states that linger. `partially_completed` rides the failed window:
# a contradicted read-back deserves the same day of attention a failure gets.
_RECENT_FAILED = ("failed", "timed_out", "partially_completed")
_RECENT_DONE = ("completed",)


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
    """One `run` Unit per active run, plus each run that recently finished.

    "Finished" covers both outcomes on purpose. A success that vanishes the
    moment it lands leaves the workspace unable to say what muldro did.
    """
    failed_cutoff = now - timedelta(hours=FAILED_RUN_WINDOW_HOURS)
    done_cutoff = now - timedelta(hours=COMPLETED_RUN_WINDOW_HOURS)
    try:
        result = await db.execute(
            select(TaskRun, Plan.goal)
            .outerjoin(Plan, TaskRun.plan_id == Plan.plan_id)
            .where(
                TaskRun.workspace_id == workspace_id,
                TaskRun.source != "user_message",
                (TaskRun.status.in_(_ACTIVE))
                | (TaskRun.status.in_(_RECENT_FAILED) & (TaskRun.updated_at >= failed_cutoff))
                | (TaskRun.status.in_(_RECENT_DONE) & (TaskRun.updated_at >= done_cutoff)),
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


# How far back the feed shows muldro's own conclusions. They carry a 1-day TTL
# of their own, so this only decides how much of that day is on the workspace.
INSIGHT_WINDOW_HOURS = 24

# Ceiling per feed build. A conclusion is worth more than an email and there
# should never be many, so this is a runaway guard rather than a budget.
MAX_INSIGHT_UNITS = 10

# `store_briefing_memory` writes these, from two places: the relevance
# assessor routing a signal to the `briefing` tier, and the Planner's
# non-actionable branch keeping a cross-cutting insight rather than discarding
# it. Both are muldro's OWN reasoning about what it saw.
_INSIGHT_MEMORY_TYPE = "briefing_item"


def _insight_headline(text: str) -> str:
    """The conclusion's first sentence, as a headline.

    Not a truncation of the body: the first sentence of a conclusion IS the
    claim, and the rest is support. `frame_for_row` neutralizes it — this text
    is model-authored, and while muldro wrote it, it wrote it ABOUT external
    content and must not be trusted to have kept markdown out.
    """
    first = (text or "").strip().split("\n", 1)[0].strip()
    for stop in (". ", "? ", "! "):
        head, sep, _ = first.partition(stop)
        if sep and len(head) >= 20:
            return head + sep.strip()
    return first


async def insight_units(db: Any, *, workspace_id: str, user_id: str, now: datetime) -> list[Unit]:
    """What muldro CONCLUDED, as `finding` Units. Never raises.

    The soul's initiative sequence is observe -> interpret -> surface
    selectively. The view layer had observe (a card per perceived thing) and
    surface, and no home for interpret: a conclusion went into a
    `briefing_item` memory and waited for a briefing generated at 07:00. On a
    workspace with no briefing row yet, it was never seen at all.

    `finding` is the kind the contracts already reserved for this — its lede
    budget is the largest of any card kind precisely because research and
    synthesis are legitimately long — and nothing emitted one from `muldro`
    until now.

    The body is muldro's own prose, exactly as `briefing_units` treats a
    briefing's `full_text`. No Quote: a conclusion is not external text, and
    attributing it to a person would be the misattribution `run_unit` already
    refuses in the other direction.
    """
    since = now - timedelta(hours=INSIGHT_WINDOW_HOURS)
    try:
        result = await db.execute(
            select(Memory)
            .where(
                Memory.workspace_id == workspace_id,
                Memory.user_id == user_id,
                Memory.memory_type == _INSIGHT_MEMORY_TYPE,
                Memory.status == "active",
                Memory.created_at >= since,
            )
            .order_by(Memory.created_at.desc())
            .limit(MAX_INSIGHT_UNITS)
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001 - one family must not cost the feed
        logger.warning("feed_insight_read_failed workspace=%s error=%s", workspace_id, exc)
        return []

    units: list[Unit] = []
    for row in rows:
        text = (getattr(row, "fact_text", "") or "").strip()
        if not text:
            continue
        try:
            frame = frame_for_row(
                source="muldro",
                entity_type="insight",
                entity_id=row.memory_id,
                kind="finding",
                status="new",
                headline=_insight_headline(text),
                occurred_at=getattr(row, "created_at", None),
            )
        except Exception as exc:  # noqa: BLE001 - one bad row costs its own card
            logger.warning("feed_insight_unit_failed id=%r error=%s", row.memory_id, exc)
            continue
        units.append(Unit(frame=frame, body=text, quotes=()))
    return units


# The queue card counts up to this many rows; beyond it the count reads "N+".
PREPARED_QUEUE_LIMIT = 25


async def briefing_units(db: Any, *, workspace_id: str, user_id: str) -> list[Unit]:
    """The most recent briefing, as one `briefing` Unit. At most one."""
    from src.models.briefings import Briefing

    try:
        result = await db.execute(
            select(Briefing)
            .where(Briefing.workspace_id == workspace_id, Briefing.user_id == user_id)
            .order_by(Briefing.briefing_date.desc(), Briefing.created_at.desc())
            .limit(1)
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_briefing_read_failed workspace=%s error=%s", workspace_id, exc)
        return []
    if not rows:
        return []

    briefing = rows[0]
    try:
        frame = frame_for_row(
            source="muldro",
            entity_type="briefing",
            entity_id=briefing.briefing_id,
            kind="briefing",
            status="new",
            headline=getattr(briefing, "headline", "") or "",
            occurred_at=getattr(briefing, "created_at", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_briefing_unit_failed id=%s error=%s", briefing.briefing_id, exc)
        return []
    # `full_text` is the briefing's own model-authored prose: it IS a body, in
    # the one place a body already existed before bodies were written anywhere.
    return [Unit(frame=frame, body=getattr(briefing, "full_text", "") or "", quotes=())]


async def prepared_work_unit(db: Any, *, workspace_id: str, user_id: str) -> Unit | None:
    """The standing review queue, or None when nothing is waiting.

    None rather than an empty card: an idle queue should be ABSENT from the
    workspace, not a card announcing idleness.

    This Unit is load-bearing. CLAUDE.md: the prepared-work queue is the only
    place a prepared action can be acted on. Its `Review` affordance is what
    opens `UnitDetail`'s queue block (Task 12) once `SurfaceDetailModal` and
    its `queue` tab are gone.

    The affordance names `internal.approve_action` because that is the only
    approval capability CAPABILITY_CATALOG carries — there is no read-side
    `internal.list_approvals`, and inventing one is forbidden: an affordance
    whose capability does not resolve is not rendered at all.
    """
    from src.models.approvals import Approval
    from src.view.contracts import Affordance

    try:
        result = await db.execute(
            select(Approval)
            .where(
                Approval.workspace_id == workspace_id,
                Approval.user_id == user_id,
                Approval.status == "pending",
                # EVERY type the queue can actually act on, not just prepared
                # actions. The queue filtered on one type while five exist, so a
                # `filter_proposal`, a `step:*` and the Governor's plan-level
                # rows were written and rendered nowhere — and a queue nobody
                # renders looks exactly like a queue with nothing in it.
                #
                # Chat approvals are the one deliberate exclusion. They carry
                # `artifact_refs["chat"] is True`, which routes them to
                # /v1/muldro/chat/resume; the decide endpoints 409 them on
                # purpose. They are not orphaned by this — they are answered
                # inline in the conversation that raised them, which is where
                # the context to answer them lives.
                # IS DISTINCT FROM, not `!=`. A row with no `chat` key yields
                # SQL NULL from the JSONB lookup, and `NULL != 'true'` is NULL
                # rather than TRUE — so a plain inequality dropped every
                # approval that was NOT a chat one, the exact opposite of the
                # intent. Caught end to end against real SQL; a fake db would
                # have agreed with the wrong version.
                Approval.artifact_refs["chat"].astext.is_distinct_from("true"),
            )
            .order_by(Approval.created_at.desc())
            .limit(PREPARED_QUEUE_LIMIT)
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_prepared_read_failed workspace=%s error=%s", workspace_id, exc)
        return None
    if not rows:
        return None

    count = len(rows)
    # "waiting for your decision", not "prepared for your review". The queue
    # now holds every type it can act on, and only a prepared action has been
    # PREPARED — a step approval sits on a run that is already underway, and a
    # filter proposal has nothing staged at all. The old wording asserted
    # something untrue of most of its own contents.
    noun = "decision" if count == 1 else "decisions"
    capped = "+" if count >= PREPARED_QUEUE_LIMIT else ""
    try:
        frame = frame_for_row(
            source="muldro",
            entity_type="prepared_work",
            entity_id=workspace_id,
            kind="proposal",
            status="needs_you",
            headline=f"{count}{capped} {noun} waiting for you",
            occurred_at=getattr(rows[0], "created_at", None),
            event_count=count,
            affordances=[
                Affordance(capability="internal.approve_action", label="Review", variant="primary")
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_prepared_unit_failed workspace=%s error=%s", workspace_id, exc)
        return None
    return Unit(frame=frame, body="", quotes=())


async def connector_health_unit(db: Any, *, workspace_id: str) -> Unit | None:
    """One `record` Unit when sources are failing, else None.

    Replaces `_build_recommendation_surfaces`, whose only real output was this
    and whose `rec_{i}` ids were the canonical example of an id that resolves
    to nothing. The information keeps a real, deterministic key.
    """
    from src.models.perception_state import PerceptionState

    try:
        result = await db.execute(
            select(PerceptionState)
            .where(
                PerceptionState.workspace_id == workspace_id,
                PerceptionState.circuit_state == "open",
            )
            .limit(10)
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_connector_health_failed workspace=%s error=%s", workspace_id, exc)
        return None
    if not rows:
        return None

    sources = sorted({str(getattr(r, "source", "")) for r in rows if getattr(r, "source", "")})
    count = len(sources)
    noun = "source" if count == 1 else "sources"
    try:
        frame = frame_for_row(
            source="muldro",
            entity_type="connector_health",
            entity_id=workspace_id,
            kind="record",
            status="failed",
            headline=f"{count} data {noun} not responding: {', '.join(sources)}",
            occurred_at=None,
            event_count=max(1, count),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feed_connector_health_unit_failed error=%s", exc)
        return None
    return Unit(frame=frame, body="", quotes=())
