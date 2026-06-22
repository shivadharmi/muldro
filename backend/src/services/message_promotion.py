"""Agent-message workspace-feed promotion gate.

The Presenter always produces a chat response, but only some of those
responses are rich enough to deserve a spot in the workspace feed. This
module encodes the structural heuristic: plain-text replies stay in
chat; responses containing tables, charts, multi-section analysis, or
actionable structural components get promoted to ``message`` surfaces.

Design decision: the gate is **structural**, not semantic. The agent
does not self-evaluate "usefulness" — it just composes. The gate looks
at what was actually built. This avoids both false positives (agent
overestimates its own importance) and false negatives (agent
underestimates multi-part analysis).
"""

from __future__ import annotations

from typing import Iterable

_STRUCTURAL_COMPONENT_TYPES: frozenset[str] = frozenset(
    {
        "Table",
        "DataGrid",
        "Chart",
        "Timeline",
        "KanbanBoard",
        "Calendar",
        "Metric",
        "ExecutionTrace",
        "EntityCard",
        "MemoryCard",
    }
)
"""Components that carry enough information density to justify a workspace card.

Text, Badge, Button etc. are excluded — on their own they belong in the
chat panel, not the workspace grid.
"""


def has_structural_component(children: Iterable[dict | object]) -> bool:
    """Return True if any child in the tree is a structural component."""

    def _walk(node: dict | object) -> bool:
        node_type = _component_type(node)
        if node_type in _STRUCTURAL_COMPONENT_TYPES:
            return True
        for child in _children(node):
            if _walk(child):
                return True
        return False

    return any(_walk(c) for c in children)


def count_sections(children: Iterable[dict | object]) -> int:
    """Count top-level Card/Column sections — a rough proxy for multi-part analysis.

    A ``Card`` containing only a single ``Text`` child is not counted; we
    treat it as a formatting wrapper. A ``Card`` with multiple children,
    or nested structural content, counts as a section.
    """
    count = 0
    for c in children:
        ctype = _component_type(c)
        if ctype in ("Card", "Column"):
            child_list = list(_children(c))
            if len(child_list) >= 2 or any(
                _component_type(gc) in _STRUCTURAL_COMPONENT_TYPES for gc in child_list
            ):
                count += 1
    return count


def should_promote_to_workspace(
    children: Iterable[dict | object],
    *,
    explicit_flag: bool = False,
) -> bool:
    """Apply the structural gate.

    A surface is promoted to the workspace feed when:
      * the agent explicitly flags it (``explicit_flag=True``), OR
      * the children contain at least one structural component, OR
      * the children contain at least two distinct sections.

    Plain-text replies fall through all three checks and stay chat-only.
    """
    if explicit_flag:
        return True

    materialized = list(children)
    if has_structural_component(materialized):
        return True
    if count_sections(materialized) >= 2:
        return True
    return False


def _component_type(node: dict | object) -> str:
    if isinstance(node, dict):
        return str(node.get("type", ""))
    return str(getattr(node, "type", ""))


def _children(node: dict | object) -> list:
    if isinstance(node, dict):
        return list(node.get("children") or [])
    return list(getattr(node, "children", []) or [])
