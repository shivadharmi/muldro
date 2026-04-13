"""Surface spec extraction utilities.

Parses structured surface directives embedded in Presenter agent responses.
Used by the execution pipeline to derive surface kind and preview data from
fenced code blocks rather than hardcoded heuristics.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.contracts import SurfaceSpec

logger = logging.getLogger(__name__)

_SURFACE_SPEC_RE = re.compile(r"```json:surface\s*\n(.*?)\n```", re.DOTALL)
_SURFACE_DATA_RE = re.compile(r"```json:surface_data\s*\n(.*?)\n```", re.DOTALL)


def extract_surface_spec(response_text: str) -> SurfaceSpec | None:
    """Extract SurfaceSpec from ```json:surface``` block in Presenter response.

    Returns SurfaceSpec on success, None if not found or invalid.
    Best-effort — degrades to chat-only on failure.
    """
    from src.orchestrator.contracts import SurfaceSpec

    match = _SURFACE_SPEC_RE.search(response_text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        return SurfaceSpec(**data)
    except (json.JSONDecodeError, Exception):
        logger.debug("Failed to parse SurfaceSpec from response", exc_info=True)
        return None


def extract_surface_data(response_text: str) -> dict | None:
    """Extract structured data from ```json:surface_data``` block.

    Used by detail tab builders for comparison, table, timeline, checklist kinds.
    """
    match = _SURFACE_DATA_RE.search(response_text)
    if not match:
        return None

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.debug("Failed to parse surface_data from response", exc_info=True)
        return None
