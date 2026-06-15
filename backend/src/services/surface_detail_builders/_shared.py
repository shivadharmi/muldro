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
    payload = _get_payload(surface)
    meta = payload.get("metadata", {})
    return payload.get("source_run_id") or meta.get("source_run_id") or meta.get("run_id")


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
