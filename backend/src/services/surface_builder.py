"""Unified surface builder — converts DB state into workspace surfaces.

Returns WorkspaceSurfaceData dicts with preview + detail_config for
the two-layer surface model. No legacy A2UISurface children.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.task_graph import TaskRun
from src.models.ui_state import UISurface
from src.ui.contracts import SurfaceMetric, SurfacePreview
from src.ui.renderer import build_detail_config

logger = logging.getLogger(__name__)


class SurfaceService:
    """Builds workspace surfaces from current DB state."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def build_workspace_surfaces(self, user_id: str) -> list[dict[str, Any]]:
        """Build all workspace surfaces for the current user.

        Returns a priority-ordered list of surface dicts, each with:
        id, kind, preview, detail_config, created_at, etc.
        """
        surfaces: list[dict[str, Any]] = []

        surfaces.extend(await self._build_approval_surfaces(user_id))
        surfaces.extend(await self._build_priority_surfaces())

        briefing = await self._build_briefing_surface(user_id)
        if briefing:
            surfaces.append(briefing)

        surfaces.extend(await self._build_recommendation_surfaces())
        surfaces.extend(await self._load_persisted_surfaces(user_id))

        return surfaces

    async def _build_approval_surfaces(self, user_id: str) -> list[dict[str, Any]]:
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(10)
        )
        approvals = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for apr in approvals:
            surface_id = f"approval_{apr.approval_id}"
            risk_level = apr.risk_level or "medium"
            risk_variant = "warning" if risk_level in ("high", "critical") else "default"

            preview = SurfacePreview(
                title=apr.title or "Pending Approval",
                subtitle=apr.summary[:120] if apr.summary else None,
                status="awaiting_approval",
                priority="high" if risk_level in ("high", "critical") else "medium",
                metrics=[
                    SurfaceMetric(label="Risk", value=risk_level, variant=risk_variant),
                ],
            )
            detail_config = build_detail_config("approval", surface_id)

            surfaces.append(
                {
                    "id": surface_id,
                    "kind": "approval",
                    "preview": preview.model_dump(mode="json"),
                    "detail_config": (
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                    "created_at": apr.created_at.isoformat() if apr.created_at else None,
                }
            )

        return surfaces

    async def _build_briefing_surface(self, user_id: str) -> dict[str, Any] | None:
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

        return {
            "id": surface_id,
            "kind": "briefing",
            "preview": preview.model_dump(mode="json"),
            "detail_config": detail_config.model_dump(mode="json") if detail_config else None,
            "created_at": briefing.created_at.isoformat() if briefing.created_at else None,
        }

    async def _build_priority_surfaces(self) -> list[dict[str, Any]]:
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["awaiting_approval", "blocked"]),
            )
            .order_by(TaskRun.created_at.desc())
            .limit(5)
        )
        runs = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for run in runs:
            short_id = run.run_id[:16]
            surface_id = f"priority_{run.run_id}"

            preview = SurfacePreview(
                title=f"Blocked: {short_id}...",
                subtitle=f"Run is {run.status}",
                status=run.status,
                priority="high",
                metrics=[SurfaceMetric(label="Status", value=run.status, variant="warning")],
            )
            detail_config = build_detail_config("alert", surface_id)

            surfaces.append(
                {
                    "id": surface_id,
                    "kind": "alert",
                    "preview": preview.model_dump(mode="json"),
                    "detail_config": (
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                }
            )

        return surfaces

    async def _build_recommendation_surfaces(self) -> list[dict[str, Any]]:
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

        surfaces: list[dict[str, Any]] = []
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
                {
                    "id": surface_id,
                    "kind": "recommendation",
                    "preview": preview.model_dump(mode="json"),
                    "detail_config": (
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                }
            )

        return surfaces

    async def _load_persisted_surfaces(self, user_id: str) -> list[dict[str, Any]]:
        """Load non-expired persisted surfaces from ui_surfaces table."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(UISurface)
            .where(
                UISurface.user_id == user_id,
                UISurface.workspace_id == self._workspace_id,
                UISurface.expires_at > now,
            )
            .order_by(UISurface.updated_at.desc())
            .limit(20)
        )
        rows = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for db_row in rows:
            try:
                payload = db_row.payload or {}
                preview_data = db_row.preview or payload.get("preview")
                if not preview_data:
                    continue

                surfaces.append(
                    {
                        "id": payload.get("id", db_row.surface_id),
                        "kind": payload.get("kind", db_row.surface_type),
                        "preview": preview_data,
                        "detail_config": db_row.detail_config or payload.get("detail_config"),
                        "decision": payload.get("decision"),
                        "source_run_id": payload.get("source_run_id"),
                        "response_preview": payload.get("response_preview"),
                        "created_at": (
                            db_row.created_at.isoformat() if db_row.created_at else None
                        ),
                    }
                )
            except Exception:
                logger.debug(
                    "Failed to parse persisted surface %s", db_row.surface_id, exc_info=True
                )

        return surfaces
