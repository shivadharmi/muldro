"""Pure helper functions shared across the graph-execution collaborators.

Extracted from ``graph_executor.py`` (god-object decomposition, 2026-06-20) so
that both the coordinator (``GraphExecutor``) and the extracted ``DagRunner`` can
import them without a circular dependency (the coordinator imports ``DagRunner``;
if these helpers stayed in ``graph_executor`` the runner would have to import back
up). This is a leaf module — it imports only contracts/errors/observability and is
imported by everything else in the cluster.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.contracts import StepState, step_status_to_ui
from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id

if TYPE_CHECKING:
    from src.models.task_graph import TaskStep


class CancellationRequested(Exception):  # noqa: N818
    """Raised when a run cancellation token is set.

    Re-homed here from ``agent_loop`` (Step 11 Phase 4) so ``dag_runner`` and the
    autonomous executors can raise/catch it without importing the legacy engine.
    """

    pass


def _check_cancellation(cancel_event: asyncio.Event | None) -> None:
    """Check cancellation token between tool rounds. Raises if set."""
    if cancel_event and cancel_event.is_set():
        raise CancellationRequested("Run cancelled by user")


def _compute_retry_delay(retry_count: int) -> int:
    """Compute exponential backoff delay in seconds, capped at 30."""
    return min(2**retry_count, 30)


def _detect_auth_required(output: dict | None) -> dict | None:
    """Detect an OAuth ``auth_required`` signal in a step's tool output.

    The external-MCP tool path returns, on a permanent OAuth failure, a
    structured dict ``{"status":"error","error_code":"auth_required",
    "provider":<p>,"server":<s>}``. The step runner may surface that either at
    the top level of the step output (minimal/fallback path) OR nested under an
    ``auth_required`` key (agent-loop path, which wraps the offending
    ``LoopToolResult``). Returns the auth dict (with ``provider``/``server``)
    when found, else ``None``.
    """
    if not isinstance(output, dict):
        return None
    nested = output.get("auth_required")
    if isinstance(nested, dict) and nested.get("error_code") == "auth_required":
        return nested
    if output.get("error_code") == "auth_required":
        return output
    return None


def _safe_error_fields(exc: BaseException) -> dict:
    """Build the client-safe error fields for run.error / step.error /
    step.output_data and any event payload that reaches a surface.

    The raw ``str(exc)`` is for logs only (and the secret-redacted trace) — it
    is NEVER placed in these fields. Returns the safe message, a stable error
    code, and a correlation id so a user can quote it to support.
    """
    code, message, _ = classify(exc)
    return {
        "message": message,
        "error_code": code,
        "correlation_id": get_correlation_id() or new_correlation_id(),
    }


def _step_to_state(s: "TaskStep", status_override: str | None = None) -> "StepState":
    """Build a StepState from a TaskStep model, forwarding all available fields.

    ``status_override`` (when given) is already a UI literal; the persisted
    ``s.status`` is a DB execution-state value and must be mapped to the UI
    vocabulary or the strict ``StepState.status`` Literal will reject it.
    """
    status = status_override or step_status_to_ui(s.status)
    started_iso = s.started_at.isoformat() if s.started_at else None
    completed_iso = s.completed_at.isoformat() if s.completed_at else None
    duration = (
        int((s.completed_at - s.started_at).total_seconds() * 1000)
        if s.completed_at and s.started_at
        else None
    )
    return StepState(
        step_id=s.step_id,
        description=s.name or (s.input_data or {}).get("capability", s.task_id),
        status=status,
        output_summary=(str(s.output_data.get("result", "")) if s.output_data else None),
        duration_ms=duration,
        started_at=started_iso,
        completed_at=completed_iso,
        timeout_seconds=s.timeout_seconds,
        error=s.error,
        retry_count=s.retry_count if s.retry_count > 0 else None,
    )
