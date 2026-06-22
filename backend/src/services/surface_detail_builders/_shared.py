"""Shared helpers for surface detail tab builders."""

from datetime import datetime
from typing import Any

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse


def _section(
    sid: str,
    title: str,
    children: list[A2UIComponent],
    collapsed: bool = True,
) -> DetailSection:
    return DetailSection(id=sid, title=title, collapsed=collapsed, children=children)


def _empty_tab(tab_id: str, message: str = "No data available.") -> DetailTabResponse:
    return DetailTabResponse(
        tab_id=tab_id,
        sections=[_section("empty", "Info", [r.text("empty_msg", message)], collapsed=False)],
    )


def _get_payload(surface: Any) -> dict:
    return getattr(surface, "payload", None) or {}


def _extract_run_id(surface: Any) -> str | None:
    """Resolve the run_id a run/summary surface points at.

    Priority: explicit linkage in the payload (``source_run_id`` /
    ``metadata.source_run_id`` / ``metadata.run_id``), then a fallback derived
    from the surface_id itself. Post-``4893e16`` the unified ``run`` surface id
    IS the run_id (``run_<ULID>``), so it is used VERBATIM — never stripped.
    Cross-prefix ids (``summary_run_<ULID>``) strip only the outer ``summary_``
    to recover the ``run_<ULID>`` underneath. This mirrors ``_resolve_ephemeral``
    in routes_surface_detail so the persisted-surface path (whose payload may
    lack explicit linkage, e.g. emit_surface_update's ``last_surface_update``)
    and the ephemeral path resolve to the same run.
    """
    payload = _get_payload(surface)
    meta = payload.get("metadata", {})
    explicit = payload.get("source_run_id") or meta.get("source_run_id") or meta.get("run_id")
    if explicit:
        return explicit

    surface_id = getattr(surface, "surface_id", "") or ""
    if surface_id.startswith("summary_"):
        return surface_id.removeprefix("summary_")
    if surface_id.startswith("run_"):
        # The run surface id IS the run_id — do NOT strip the ``run_`` prefix,
        # it is part of the id (stripping it was the "not found" bug).
        return surface_id
    return None


def _extract_approval_id(surface: Any) -> str | None:
    surface_id = getattr(surface, "surface_id", "") or ""
    if surface_id.startswith("approval_"):
        return surface_id.removeprefix("approval_")
    if surface_id.startswith("notif_surf_"):
        payload = _get_payload(surface)
        return payload.get("metadata", {}).get("approval_id")
    return None


def _extract_briefing_id(surface: Any) -> str | None:
    surface_id = getattr(surface, "surface_id", "") or ""
    if surface_id.startswith("briefing_"):
        return surface_id.removeprefix("briefing_")
    payload = _get_payload(surface)
    return payload.get("metadata", {}).get("briefing_id")


async def _resolve_briefing(surface: Any, db: Any):
    """Resolve the ``Briefing`` for a surface.

    Briefing-kind surfaces can originate from two places:
    1. ``SurfaceService._build_briefing_surface`` — id is ``briefing_{id}`` so
       ``_extract_briefing_id`` yields the id directly.
    2. ``surface_pusher.push_workspace_surface`` — a Presenter-derived surface
       persisted with id ``surf_{ULID}`` and **no** ``briefing_id`` in its
       payload. For these we fall back to the most recent briefing for the
       surface's owner (user/workspace scoped), so the visible grid card and
       its detail tabs agree on the same briefing instead of dead-ending on
       "No linked briefing found."

    Returns ``(briefing | None, had_id)`` where ``had_id`` is True when the
    surface carried a resolvable briefing id (used to disambiguate
    "id pointed at a missing briefing" from "no briefing exists yet").
    """
    from sqlalchemy import select

    from src.models.briefings import Briefing

    briefing_id = _extract_briefing_id(surface)
    if briefing_id:
        result = await db.execute(select(Briefing).where(Briefing.briefing_id == briefing_id))
        return result.scalar_one_or_none(), True

    user_id = getattr(surface, "user_id", None)
    if not user_id:
        return None, False

    stmt = select(Briefing).where(Briefing.user_id == user_id)
    workspace_id = getattr(surface, "workspace_id", None)
    if workspace_id:
        stmt = stmt.where(Briefing.workspace_id == workspace_id)
    stmt = stmt.order_by(Briefing.briefing_date.desc(), Briefing.created_at.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none(), False


def _get_step_desc(step: Any) -> str:
    input_data = getattr(step, "input_data", None)
    if isinstance(input_data, dict):
        return input_data.get("description", "")
    return ""


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_ts(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")
