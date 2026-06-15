"""Unified surface builder — converts DB state into workspace surfaces.

Returns WorkspaceSurfacePush models with preview + detail_config for
the two-layer surface model. No legacy A2UISurface children.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contracts import WorkspaceSurfacePush
from src.models.briefings import Briefing
from src.models.task_graph import TaskRun, TaskStep
from src.models.trust_state import TrustState
from src.models.ui_state import UISurface
from src.services.surface_mapping import apply_surface_cap
from src.ui.contracts import SurfaceMetric, SurfacePreview
from src.ui.renderer import build_detail_config

logger = logging.getLogger(__name__)


class SurfaceService:
    """Builds workspace surfaces from current DB state."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def build_workspace_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
        """Build all workspace surfaces for the current user.

        Returns a priority-ordered list of WorkspaceSurfacePush models. The
        unified ``run`` surface replaces the former ``exec``/``plan``/
        ``approval`` trio — approvals are now embedded inside the run
        surface's inline approval card (see ui/units.approval_card).
        """
        surfaces: list[WorkspaceSurfacePush] = []

        surfaces.extend(await self._build_run_surfaces())

        briefing = await self._build_briefing_surface(user_id)
        if briefing:
            surfaces.append(briefing)

        surfaces.extend(await self._build_insight_surfaces(user_id))
        surfaces.extend(await self._build_recommendation_surfaces())
        surfaces.extend(await self._load_persisted_surfaces(user_id))

        return apply_surface_cap(surfaces)

    async def _get_trust_context(self, approval) -> dict[str, str] | None:
        """Build trust context dict from approval artifact_refs."""
        refs = approval.artifact_refs
        if not refs or not isinstance(refs, dict):
            return None

        capability = refs.get("tool_name")
        if not capability:
            return None

        risk_level = approval.risk_level or "low"

        result = await self._db.execute(
            select(TrustState).where(
                TrustState.workspace_id == self._workspace_id,
                TrustState.capability == capability,
                TrustState.risk_level == risk_level,
            )
        )
        state = result.scalar_one_or_none()
        if not state:
            return {
                "trust_level": "first_use",
                "label": "First time",
                "variant": "default",
            }

        level = state.trust_level
        approved = state.approved_count

        if level == "first_use":
            return {
                "trust_level": "first_use",
                "label": "First time",
                "variant": "default",
            }
        elif level == "learning":
            remaining = 10 - approved
            hint = f"{remaining} more to auto-approve" if remaining > 0 else ""
            return {
                "trust_level": "learning",
                "label": f"Similar to {approved} approvals",
                "variant": "default",
                "graduation_hint": hint,
            }

        return {
            "trust_level": level,
            "label": level.replace("_", " ").title(),
            "variant": "success" if level in ("trusted", "autonomous") else "default",
        }

    async def _build_briefing_surface(self, user_id: str) -> WorkspaceSurfacePush | None:
        today = date.today()
        result = await self._db.execute(
            select(Briefing).where(
                Briefing.user_id == user_id,
                Briefing.briefing_date == today,
            )
        )
        briefing = result.scalar_one_or_none()
        if not briefing:
            return None

        surface_id = f"briefing_{briefing.briefing_id}"
        priorities = briefing.top_priorities or []
        actions = briefing.recommended_actions or []

        # First priority title as subtitle for context
        first_priority = ""
        if priorities:
            p = priorities[0]
            first_priority = p.get("title", "") if isinstance(p, dict) else str(p)

        preview = SurfacePreview(
            title=briefing.headline or "Daily Briefing",
            subtitle=first_priority[:100] if first_priority else None,
            metrics=[
                SurfaceMetric(label="Priorities", value=str(len(priorities))),
                SurfaceMetric(label="Actions", value=str(len(actions))),
            ],
            tags=["briefing"],
        )
        detail_config = build_detail_config("briefing", surface_id)

        return WorkspaceSurfacePush(
            id=surface_id,
            kind="briefing",
            preview=preview.model_dump(mode="json"),
            detail_config=detail_config.model_dump(mode="json") if detail_config else None,
            created_at=briefing.created_at.isoformat() if briefing.created_at else "",
        )

    async def _build_run_surfaces(self) -> list[WorkspaceSurfacePush]:
        """Build unified ``run`` surfaces for every active TaskRun.

        Active = ``running | paused | awaiting_approval | blocked``. Each run
        yields exactly one surface with id ``run_{run_id}`` — matching the
        surface_id used by the live WebSocket push from ``GraphExecutor``, so
        the frontend deduplicates REST and WS updates naturally.

        Approval state is reflected in the preview (status + priority); the
        full approval card is rendered inline inside the surface's child
        components (composed from ``ui/units.approval_card``) rather than as
        a standalone surface.
        """
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["running", "paused", "awaiting_approval", "blocked"]),
                TaskRun.source != "user_message",
            )
            .order_by(TaskRun.started_at.desc())
            .limit(10)
        )
        runs = result.scalars().all()
        surfaces: list[WorkspaceSurfacePush] = []

        for run in runs:
            step_result = await self._db.execute(
                select(TaskStep).where(TaskStep.run_id == run.run_id).order_by(TaskStep.created_at)
            )
            steps = list(step_result.scalars().all())
            completed = sum(1 for s in steps if s.status == "completed")
            total = len(steps)

            current_step_name = None
            for s in steps:
                if s.status in ("running", "ready"):
                    current_step_name = s.name or (s.input_data or {}).get("capability", "")
                    break

            surface_id = f"run_{run.run_id}"
            subtitle = f"Step {completed + 1}/{total}" if total else "No steps yet"
            if current_step_name:
                subtitle += f": {current_step_name}"

            awaiting = run.status in ("awaiting_approval", "blocked")
            status_value = "awaiting_approval" if awaiting else "running"
            priority = "high" if awaiting else "medium"

            metrics = [
                SurfaceMetric(
                    label="Progress",
                    value=f"{completed}/{total} steps" if total else "—",
                ),
            ]
            if run.status == "awaiting_approval":
                metrics.append(
                    SurfaceMetric(label="Status", value="awaiting approval", variant="warning")
                )
            elif run.status == "blocked":
                metrics.append(SurfaceMetric(label="Status", value="blocked", variant="danger"))

            # Derive a readable title from the first step name if available,
            # otherwise fall back to a generic "Run" label.
            first_step_name = ""
            for s in steps:
                if s.name:
                    first_step_name = s.name
                    break
            title = first_step_name or "Run"

            preview = SurfacePreview(
                title=title,
                subtitle=subtitle,
                status=status_value,
                priority=priority,
                progress=completed / total if total > 0 else 0.0,
                metrics=metrics,
            )
            detail_config = build_detail_config("run", surface_id)

            surfaces.append(
                WorkspaceSurfacePush(
                    id=surface_id,
                    kind="run",
                    preview=preview.model_dump(mode="json"),
                    detail_config=(
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                    source_run_id=run.run_id,
                    created_at=(
                        run.started_at.isoformat() if run.started_at else run.created_at.isoformat()
                    ),
                )
            )

        return surfaces

    async def _build_recommendation_surfaces(self) -> list[WorkspaceSurfacePush]:
        actions: list[dict] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        failed_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status == "failed",
                TaskRun.updated_at >= cutoff,
            )
        )
        failed_count = failed_result.scalar() or 0
        if failed_count > 0:
            actions.append(
                {
                    "title": f"Investigate {failed_count} failed"
                    f" run{'s' if failed_count > 1 else ''}",
                    "description": "Recent workflow failures may need your attention.",
                    "priority": "medium",
                }
            )

        try:
            from src.models.perception_state import PerceptionState

            stale_result = await self._db.execute(
                select(PerceptionState)
                .where(
                    PerceptionState.workspace_id == self._workspace_id,
                    PerceptionState.circuit_state == "open",
                )
                .limit(5)
            )
            stale = list(stale_result.scalars().all())
            if stale:
                sources = [s.source for s in stale]
                actions.append(
                    {
                        "title": f"{len(stale)} data source{'s' if len(stale) > 1 else ''} failing",
                        "description": f"Sources with errors: {', '.join(sources)}",
                        "priority": "high",
                    }
                )
        except Exception:
            logger.debug("Failed to check observation status", exc_info=True)

        surfaces: list[WorkspaceSurfacePush] = []
        for i, action in enumerate(actions[:3]):
            surface_id = f"rec_{i}"
            priority = action.get("priority", "medium")

            preview = SurfacePreview(
                title=action["title"],
                subtitle=action["description"][:120],
                priority=priority if priority != "medium" else None,
                tags=["recommendation"],
            )
            detail_config = build_detail_config("recommendation", surface_id)

            surfaces.append(
                WorkspaceSurfacePush(
                    id=surface_id,
                    kind="recommendation",
                    preview=preview.model_dump(mode="json"),
                    detail_config=(
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                )
            )

        return surfaces

    async def _build_insight_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
        """Load persisted proactive insight surfaces that haven't expired."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(UISurface)
            .where(
                UISurface.user_id == user_id,
                UISurface.workspace_id == self._workspace_id,
                UISurface.surface_type == "proactive_insight",
                UISurface.expires_at > now,
            )
            .order_by(UISurface.updated_at.desc())
            .limit(10)
        )
        rows = result.scalars().all()
        surfaces: list[WorkspaceSurfacePush] = []

        for db_row in rows:
            try:
                payload = db_row.payload or {}
                preview_data = db_row.preview or payload.get("preview")
                if not preview_data:
                    continue

                surfaces.append(
                    WorkspaceSurfacePush(
                        id=payload.get("id", db_row.surface_id),
                        kind="proactive_insight",
                        preview=preview_data,
                        detail_config=None,
                        insight_data=payload.get("insight_data"),
                        created_at=(db_row.created_at.isoformat() if db_row.created_at else ""),
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to parse insight surface %s",
                    db_row.surface_id,
                    exc_info=True,
                )

        return surfaces

    async def _load_persisted_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
        """Load non-expired persisted surfaces from ui_surfaces table."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(UISurface)
            .where(
                UISurface.user_id == user_id,
                UISurface.workspace_id == self._workspace_id,
                UISurface.expires_at > now,
                UISurface.surface_type != "proactive_insight",
            )
            .order_by(UISurface.updated_at.desc())
            .limit(20)
        )
        rows = result.scalars().all()
        surfaces: list[WorkspaceSurfacePush] = []

        for db_row in rows:
            try:
                payload = db_row.payload or {}
                preview_data = db_row.preview or payload.get("preview")
                if not preview_data:
                    continue

                # Forward persisted execution state so REST clients have
                # phase/steps/approval data without waiting for a WS update.
                last_update = payload.get("last_surface_update")
                exec_fields: dict = {}
                if last_update and isinstance(last_update, dict):
                    for key in (
                        "phase",
                        "steps",
                        "current_step",
                        "progress",
                        "approval",
                        "results",
                    ):
                        if key in last_update:
                            exec_fields[key] = last_update[key]

                surfaces.append(
                    WorkspaceSurfacePush(
                        id=payload.get("id", db_row.surface_id),
                        kind=payload.get("kind", db_row.surface_type),
                        preview=preview_data,
                        detail_config=db_row.detail_config or payload.get("detail_config"),
                        source_run_id=payload.get("source_run_id"),
                        response_preview=payload.get("response_preview"),
                        created_at=(db_row.created_at.isoformat() if db_row.created_at else ""),
                        surface_data=payload.get("surface_data"),
                        **exec_fields,
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to parse persisted surface %s", db_row.surface_id, exc_info=True
                )

        return surfaces
