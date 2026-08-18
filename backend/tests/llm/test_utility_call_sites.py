"""Every utility-LLM call site must pass ``workspace_id``.

The shared ``complete_text`` / ``complete_text_with_usage`` seam resolves a model
against a workspace's bindings. A call site that omits ``workspace_id`` silently
resolves against the deployment default — the workspace's configured model is
ignored while its tokens are still billed to that workspace. That failure is
invisible at runtime (no error, just the wrong model), so it is locked here rather
than left to review.

An AST scan, not a grep: it sees keyword arguments, not text, so a call spread
across lines or reordered still counts.
"""

from __future__ import annotations

import ast
import pathlib

_SEAM_FUNCS = {"complete_text", "complete_text_with_usage"}
_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
# The seam itself defines the parameter; it has nothing to forward it to.
_SEAM_MODULE = _SRC / "llm" / "utility.py"


def _call_sites_missing_workspace_id() -> list[str]:
    missing: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == _SEAM_MODULE:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _SEAM_FUNCS
                and not any(kw.arg == "workspace_id" for kw in node.keywords)
            ):
                missing.append(f"{path.relative_to(_SRC.parent)}:{node.lineno} {node.func.id}(...)")
    return missing


def test_every_utility_llm_call_site_passes_workspace_id():
    missing = _call_sites_missing_workspace_id()
    assert not missing, (
        "utility-LLM call sites missing workspace_id (workspace model overrides will be "
        "silently ignored there):\n  " + "\n  ".join(missing)
    )
