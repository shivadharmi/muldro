"""R3b: the repair loop records whether it actually repairs.

``MAX_ATTEMPTS = 3`` in ``repair_cap`` is a judgement made on n=2 live observations. These
tests pin the three outcomes that let it be revisited with data:

* ``rejected``  — a dispatched call came back ``invalid_tool_args`` (every failure, first included)
* ``repaired``  — a tool that had failed earlier this turn subsequently dispatched successfully
* ``exhausted`` — the cap refused to dispatch

Prometheus counters are process-global and accumulate across the whole session, so nothing
here reads an absolute value: every assertion is a BEFORE/AFTER delta, and every test uses a
tool name unique to itself. Both together make these tests order-independent.
"""

from __future__ import annotations

import json
from unittest.mock import create_autospec
from uuid import uuid4

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from prometheus_client import REGISTRY

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.deep_runtime.middleware.repair_cap import MAX_ATTEMPTS, make_repair_cap_middleware

_METRIC = "muldro_tool_arg_repair_total"
_OUTCOMES = ("rejected", "repaired", "exhausted")


def _tool(label: str) -> str:
    """A tool name unique to one test run — no bleed between tests or sessions."""
    return f"{label}_{uuid4().hex[:8]}"


def _counts(tool: str) -> dict[str, float]:
    """Current counter value per outcome for ``tool`` (0.0 for a label set never touched)."""
    return {
        outcome: REGISTRY.get_sample_value(_METRIC, {"tool": tool, "outcome": outcome}) or 0.0
        for outcome in _OUTCOMES
    }


def _delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {outcome: after[outcome] - before[outcome] for outcome in _OUTCOMES}


def _request(name: str, call_id: str = "tc1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


def _invalid_args_message(name: str, call_id: str = "tc1") -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "error": f"Invalid argument(s) for '{name}': subtitle: too long.",
                "error_code": "invalid_tool_args",
            }
        ),
        tool_call_id=call_id,
        name=name,
        status="error",
    )


def _ok_message(name: str, call_id: str = "tc1") -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"ok": True}), tool_call_id=call_id, name=name, status="success"
    )


async def _reference_handler(request: ToolCallRequest) -> ToolMessage:  # pragma: no cover
    """Signature source for the autospec'd downstream handler double."""
    raise AssertionError("reference handler is a signature source only")


def _handler(*results: ToolMessage):
    handler = create_autospec(_reference_handler)
    handler.side_effect = list(results)
    return handler


# ---------------------------------------------------------------------------
# rejected
# ---------------------------------------------------------------------------


async def test_invalid_args_rejection_counts_rejected_once():
    """Every argument-validation failure counts — including the very first one."""
    tool = _tool("render_surface")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    await mw.awrap_tool_call(_request(tool), _handler(_invalid_args_message(tool)))

    assert _delta(before, _counts(tool)) == {"rejected": 1.0, "repaired": 0.0, "exhausted": 0.0}


async def test_every_failure_counts_rejected_not_just_the_last_one():
    """Three failures below the cap are three rejections — the denominator must be honest."""
    tool = _tool("render_surface")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    handler = _handler(*(_invalid_args_message(tool) for _ in range(MAX_ATTEMPTS)))
    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request(tool), handler)

    assert _delta(before, _counts(tool)) == {
        "rejected": float(MAX_ATTEMPTS),
        "repaired": 0.0,
        "exhausted": 0.0,
    }


# ---------------------------------------------------------------------------
# repaired
# ---------------------------------------------------------------------------


async def test_success_after_a_failure_counts_repaired_once():
    """A tool that failed and then went through is the whole point of the loop."""
    tool = _tool("render_surface")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    handler = _handler(_invalid_args_message(tool), _ok_message(tool))
    await mw.awrap_tool_call(_request(tool), handler)
    await mw.awrap_tool_call(_request(tool), handler)

    assert _delta(before, _counts(tool)) == {"rejected": 1.0, "repaired": 1.0, "exhausted": 0.0}


async def test_a_first_time_success_is_not_a_repair():
    """A tool that never failed never entered the loop — counting it would inflate the ratio."""
    tool = _tool("search_memories")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    await mw.awrap_tool_call(_request(tool), _handler(_ok_message(tool)))

    assert _delta(before, _counts(tool)) == {"rejected": 0.0, "repaired": 0.0, "exhausted": 0.0}


async def test_an_unrelated_tool_error_is_neither_a_rejection_nor_a_repair():
    """auth_required is not a repairable-argument failure, and it is not a success either."""
    tool = _tool("send_email")
    before = _counts(tool)

    unrelated = ToolMessage(
        content=json.dumps({"error": "Token expired", "error_code": "auth_required"}),
        tool_call_id="tc1",
        name=tool,
        status="error",
    )
    mw = make_repair_cap_middleware()
    await mw.awrap_tool_call(_request(tool), _handler(unrelated))

    assert _delta(before, _counts(tool)) == {"rejected": 0.0, "repaired": 0.0, "exhausted": 0.0}


# ---------------------------------------------------------------------------
# exhausted
# ---------------------------------------------------------------------------


async def test_the_cap_refusing_to_dispatch_counts_exhausted_once():
    """The burning-tokens tail: MAX_ATTEMPTS rejections, then one refusal without dispatch."""
    tool = _tool("render_surface")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    handler = _handler(*(_invalid_args_message(tool) for _ in range(MAX_ATTEMPTS)))
    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request(tool), handler)

    handler.side_effect = AssertionError("capped call must not dispatch")
    await mw.awrap_tool_call(_request(tool, "tc_capped"), handler)

    # The refusal is `exhausted`, NOT another `rejected`: nothing was dispatched, so nothing
    # was rejected. Double-counting it would corrupt the denominator of repaired/rejected.
    assert _delta(before, _counts(tool)) == {
        "rejected": float(MAX_ATTEMPTS),
        "repaired": 0.0,
        "exhausted": 1.0,
    }


async def test_every_capped_call_counts_exhausted():
    """A model that keeps calling a capped tool is exactly the cost we want visible."""
    tool = _tool("render_surface")
    before = _counts(tool)

    mw = make_repair_cap_middleware()
    handler = _handler(*(_invalid_args_message(tool) for _ in range(MAX_ATTEMPTS)))
    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request(tool), handler)

    handler.side_effect = AssertionError("capped call must not dispatch")
    for _ in range(3):
        await mw.awrap_tool_call(_request(tool, "tc_capped"), handler)

    assert _delta(before, _counts(tool))["exhausted"] == 3.0


# ---------------------------------------------------------------------------
# Not counted at all
# ---------------------------------------------------------------------------


async def test_builtins_are_never_counted():
    """deepagents built-ins never reach ToolExecutor's typed-argument parse."""
    builtin = sorted(DEEPAGENTS_BUILTIN_NAMES)[0]
    before = _counts(builtin)

    mw = make_repair_cap_middleware()
    handler = _handler(*(_invalid_args_message(builtin) for _ in range(MAX_ATTEMPTS + 1)))
    for _ in range(MAX_ATTEMPTS + 1):
        await mw.awrap_tool_call(_request(builtin), handler)

    assert _delta(before, _counts(builtin)) == {
        "rejected": 0.0,
        "repaired": 0.0,
        "exhausted": 0.0,
    }


# ---------------------------------------------------------------------------
# The log line must survive the JSON formatter
# ---------------------------------------------------------------------------


async def test_outcome_log_renders_tool_and_outcome_in_the_message(caplog):
    """printf/bracket style, NOT ``extra={...}``.

    ``JSONFormatter`` serializes a 13-key allowlist that contains neither ``tool`` nor
    ``outcome``, so an ``extra=``-carried fact is silently dropped under MULDRO_LOG_JSON.
    Asserting on the RENDERED message is what makes that regression impossible.
    """
    tool = _tool("render_surface")
    mw = make_repair_cap_middleware()

    with caplog.at_level("INFO", logger="src.deep_runtime.middleware.repair_cap"):
        await mw.awrap_tool_call(_request(tool), _handler(_invalid_args_message(tool)))

    messages = [r.getMessage() for r in caplog.records]
    assert any(tool in m and "rejected" in m for m in messages), messages


# ---------------------------------------------------------------------------
# The facade itself
# ---------------------------------------------------------------------------


def test_facade_labels_the_counter_by_tool_and_outcome():
    """Pins the public entry point and its two label names."""
    from src.services.metrics_service import MetricsService

    tool = _tool("facade")
    before = _counts(tool)

    MetricsService.record_tool_arg_repair(tool=tool, outcome="repaired")

    assert _delta(before, _counts(tool)) == {"rejected": 0.0, "repaired": 1.0, "exhausted": 0.0}
