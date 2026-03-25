"""Unified surface builder — converts DB state into A2UI surfaces.

Queries the same data as routes_canvas.py and home_feed.py, but returns
pre-built A2UISurface objects with populated children[] using renderer.py
builders. The frontend renders these directly via A2UIRenderer.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.task_graph import TaskRun
from src.models.ui_state import UISurface
from src.ui import renderer as r
from src.ui.contracts import A2UISurface
from src.ui.views import briefing_full_view

logger = logging.getLogger(__name__)


class SurfaceService:
    """Builds workspace surfaces from current DB state."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def build_workspace_surfaces(self, user_id: str) -> list[A2UISurface]:
        """Build all workspace surfaces for the current user.

        Returns a priority-ordered list: approvals first, then priorities,
        briefing, recommendations, and finally persisted WS surfaces.
        """
        surfaces: list[A2UISurface] = []

        approval_surfaces = await self._build_approval_surfaces(user_id)
        surfaces.extend(approval_surfaces)

        priority_surfaces = await self._build_priority_surfaces()
        surfaces.extend(priority_surfaces)

        briefing_surface = await self._build_briefing_surface(user_id)
        if briefing_surface:
            surfaces.append(briefing_surface)

        recommendation_surfaces = await self._build_recommendation_surfaces()
        surfaces.extend(recommendation_surfaces)

        persisted = await self._load_persisted_surfaces(user_id)
        surfaces.extend(persisted)

        return surfaces

    async def _build_approval_surfaces(self, user_id: str) -> list[A2UISurface]:
        """Build one A2UI surface per pending approval."""
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
        surfaces: list[A2UISurface] = []

        for apr in approvals:
            risk_variant = "warning" if apr.risk_level in ("high", "critical") else "default"
            children = [
                r.heading(f"apr_{apr.approval_id}_title", apr.title or "Pending Approval"),
                r.badge(
                    f"apr_{apr.approval_id}_risk",
                    apr.risk_level or "medium",
                    variant=risk_variant,
                ),
                r.text(
                    f"apr_{apr.approval_id}_summary",
                    apr.summary or "No summary available.",
                ),
                r.row(
                    f"apr_{apr.approval_id}_actions",
                    [
                        r.button(
                            f"approve_{apr.approval_id}",
                            "Approve",
                            variant="primary",
                            action_payload={
                                "action": "approve",
                                "id": apr.approval_id,
                            },
                        ),
                        r.button(
                            f"reject_{apr.approval_id}",
                            "Reject",
                            variant="danger",
                            action_payload={
                                "action": "reject",
                                "id": apr.approval_id,
                            },
                        ),
                    ],
                ),
            ]
            surface = r.surface(
                f"approval_{apr.approval_id}",
                [r.card(f"apr_{apr.approval_id}_card", children)],
                metadata={
                    "kind": "approval",
                    "title": apr.title or "Pending Approval",
                    "priority": apr.risk_level or "medium",
                },
            )
            surfaces.append(surface)

        return surfaces

    async def _build_briefing_surface(self, user_id: str) -> A2UISurface | None:
        """Build a briefing surface from today's briefing using briefing_full_view."""
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

        briefing_dict = {
            "headline": briefing.headline or "No briefing yet",
            "top_priorities": briefing.top_priorities or [],
            "recommended_actions": briefing.recommended_actions or [],
        }
        surface = briefing_full_view(user_id, briefing_dict)

        # Enrich metadata for the frontend
        surface.metadata = {
            "kind": "briefing",
            "title": briefing.headline or "Daily Briefing",
            "briefing_id": briefing.briefing_id,
        }
        return surface

    async def _build_priority_surfaces(self) -> list[A2UISurface]:
        """Build alert surfaces for blocked/awaiting runs."""
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
        surfaces: list[A2UISurface] = []

        for run in runs:
            short_id = run.run_id[:16]
            surface = r.surface(
                f"priority_{run.run_id}",
                [
                    r.card(
                        f"pri_{run.run_id}_card",
                        [
                            r.alert(
                                f"pri_{run.run_id}_alert",
                                f"Run {short_id}... is {run.status}",
                                severity="warning",
                                title="Workflow Blocked",
                            ),
                        ],
                    ),
                ],
                metadata={
                    "kind": "alert",
                    "title": f"Blocked: {short_id}...",
                    "priority": "high",
                },
            )
            surfaces.append(surface)

        return surfaces

    async def _build_recommendation_surfaces(self) -> list[A2UISurface]:
        """Build recommendation cards from recommended actions logic."""
        actions: list[dict] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        # Failed runs → recommendation
        failed_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status == "failed",
                TaskRun.updated_at >= cutoff,
            )
        )
        failed_count = failed_result.scalar() or 0
        if failed_count > 0:
            actions.append({
                "title": f"Investigate {failed_count} failed run{'s' if failed_count > 1 else ''}",
                "description": "Recent workflow failures may need your attention.",
                "priority": "medium",
            })

        # Stale observations
        try:
            from src.models.observation import ObservationStatus

            stale_result = await self._db.execute(
                select(ObservationStatus)
                .where(
                    ObservationStatus.workspace_id == self._workspace_id,
                    ObservationStatus.status == "error",
                )
                .limit(5)
            )
            stale = list(stale_result.scalars().all())
            if stale:
                sources = [s.source for s in stale]
                actions.append({
                    "title": f"{len(stale)} data source{'s' if len(stale) > 1 else ''} failing",
                    "description": f"Sources with errors: {', '.join(sources)}",
                    "priority": "high",
                })
        except Exception:
            logger.debug("Failed to check observation status", exc_info=True)

        surfaces: list[A2UISurface] = []
        for i, action in enumerate(actions[:3]):
            surface = r.surface(
                f"rec_{i}",
                [
                    r.card(
                        f"rec_{i}_card",
                        [
                            r.heading(f"rec_{i}_title", action["title"]),
                            r.text(f"rec_{i}_desc", action["description"]),
                        ],
                    ),
                ],
                metadata={
                    "kind": "recommendation",
                    "title": action["title"],
                    "priority": action.get("priority", "medium"),
                },
            )
            surfaces.append(surface)

        return surfaces

    async def _load_persisted_surfaces(self, user_id: str) -> list[A2UISurface]:
        """Load non-expired persisted surfaces from ui_surfaces table.

        These are surfaces pushed via WebSocket that were persisted for
        page-refresh survival. Returns them as-is since they already
        contain A2UI component trees (after Phase 2).
        """
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
        surfaces: list[A2UISurface] = []

        for row in rows:
            try:
                payload = row.payload or {}
                surface = A2UISurface(
                    id=payload.get("id", row.surface_id),
                    children=[],
                    metadata=payload.get("metadata", {}),
                )
                # If the payload has children (from Phase 2 WS push), use them
                raw_children = payload.get("children", [])
                if raw_children:
                    from src.ui.contracts import A2UIComponent

                    surface.children = [
                        A2UIComponent.model_validate(c) for c in raw_children
                    ]
                surfaces.append(surface)
            except Exception:
                logger.debug(
                    "Failed to parse persisted surface %s", row.surface_id, exc_info=True
                )

        return surfaces
