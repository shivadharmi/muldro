"""Unit tests for composable surface units."""

import pytest

from src.contracts import ApprovalContext, InsightSurfaceData, ResultSummary, StepState
from src.ui import units
from src.ui.contracts import (
    AGENT_SURFACE_KINDS,
    SYSTEM_SURFACE_KINDS,
    is_agent_surface,
    is_system_surface,
)

# ── Kind taxonomy ───────────────────────────────────────────────────


def test_system_surface_kinds_include_run_and_summary() -> None:
    assert "run" in SYSTEM_SURFACE_KINDS
    assert "summary" in SYSTEM_SURFACE_KINDS
    assert "briefing" in SYSTEM_SURFACE_KINDS
    assert "proactive_insight" in SYSTEM_SURFACE_KINDS


def test_agent_surface_kinds_include_message() -> None:
    assert "message" in AGENT_SURFACE_KINDS
    assert "run" not in AGENT_SURFACE_KINDS


def test_is_system_vs_agent() -> None:
    assert is_system_surface("run") is True
    assert is_agent_surface("run") is False
    assert is_agent_surface("message") is True
    assert is_system_surface("message") is False


def test_require_kind_accepts_known_and_rejects_unknown() -> None:
    assert units.require_kind("run") == "run"
    assert units.require_kind("message") == "message"
    assert units.require_kind("approval") == "approval"  # legacy retained
    with pytest.raises(ValueError, match="Unknown surface kind"):
        units.require_kind("bogus")


# ── run_header ──────────────────────────────────────────────────────


def test_run_header_builds_card_with_phase_badge() -> None:
    c = units.run_header(title="Test run", phase="executing", agent_name="executor", progress="2/5")
    assert c.type == "Card"
    serialized = c.model_dump()
    assert "Test run" in str(serialized)
    assert "executing" in str(serialized)
    assert "executor" in str(serialized)
    assert "2/5" in str(serialized)


def test_run_header_maps_failed_phase_to_danger() -> None:
    c = units.run_header(title="Run", phase="failed", run_id="r1")
    # Phase badge should carry the danger variant.
    meta_row = c.children[1]
    phase_badge = meta_row.children[0]
    assert phase_badge.properties["variant"] == "danger"


def test_run_header_approval_phase_uses_warning_variant() -> None:
    c = units.run_header(title="Run", phase="approval_needed")
    phase_badge = c.children[1].children[0]
    assert phase_badge.properties["variant"] == "warning"


# ── plan_summary ────────────────────────────────────────────────────


def test_plan_summary_includes_all_provided_sections() -> None:
    c = units.plan_summary(
        goal="Ship v2",
        reasoning="Release window",
        success_criteria="All tests green",
        priority="high",
        trigger_type="user",
        run_id="r1",
    )
    s = str(c.model_dump())
    assert "Ship v2" in s
    assert "Release window" in s
    assert "All tests green" in s
    assert "priority: high" in s
    assert "trigger: user" in s


def test_plan_summary_omits_empty_sections() -> None:
    c = units.plan_summary(goal="Minimal")
    s = str(c.model_dump())
    assert "Minimal" in s
    assert "REASONING" not in s
    assert "SUCCESS CRITERIA" not in s


# ── step_list ───────────────────────────────────────────────────────


def test_step_list_renders_each_step_with_status() -> None:
    steps = [
        StepState(step_id="s1", description="Read email", status="completed", duration_ms=1200),
        StepState(step_id="s2", description="Compose reply", status="executing"),
        StepState(step_id="s3", description="Send", status="pending"),
    ]
    c = units.step_list(steps=steps, current_step="s2", run_id="r1")
    s = str(c.model_dump())
    assert "Read email" in s
    assert "Compose reply" in s
    assert "Send" in s
    assert "completed" in s
    assert "executing" in s
    assert "pending" in s


def test_step_list_coerces_dicts() -> None:
    steps = [
        {"step_id": "s1", "description": "Do thing", "status": "completed"},
    ]
    c = units.step_list(steps=steps)
    assert "Do thing" in str(c.model_dump())


def test_step_list_empty_produces_placeholder_not_error() -> None:
    c = units.step_list(steps=[])
    s = str(c.model_dump())
    assert "No steps recorded" in s


# ── approval_card ───────────────────────────────────────────────────


def test_approval_card_renders_risk_and_actions() -> None:
    ap = ApprovalContext(
        approval_id="apr_123",
        step_description="Send email to investor",
        risk_level="high",
        trust_level="learning",
        risk_reasoning="External send",
        trust_context="3/10 approvals",
        reversible=False,
        blast_radius="external",
    )
    c = units.approval_card(ap)
    s = str(c.model_dump())
    assert "apr_123" in s
    assert "Send email to investor" in s
    assert "risk: high" in s
    assert "trust: learning" in s
    assert "External send" in s
    assert "irreversible" in s
    assert "Approve" in s
    assert "Reject" in s


def test_approval_card_without_actions() -> None:
    ap = ApprovalContext(
        approval_id="apr_x",
        step_description="Action",
        risk_reasoning="r",
        trust_context="t",
    )
    c = units.approval_card(ap, include_actions=False)
    s = str(c.model_dump())
    assert "Approve" not in s


# ── results_summary ─────────────────────────────────────────────────


def test_results_summary_all_sections() -> None:
    res = ResultSummary(
        key_findings=["A", "B"],
        artifacts_created=["file.md"],
        suggested_next=["Follow up"],
    )
    c = units.results_summary(res, run_id="r1")
    s = str(c.model_dump())
    for token in ("KEY FINDINGS", "ARTIFACTS", "SUGGESTED NEXT", "A", "B", "file.md", "Follow up"):
        assert token in s


def test_results_summary_empty_shows_placeholder() -> None:
    res = ResultSummary()
    c = units.results_summary(res)
    assert "no captured outputs" in str(c.model_dump())


# ── trace_metrics ───────────────────────────────────────────────────


def test_trace_metrics_totals_and_cost() -> None:
    c = units.trace_metrics(input_tokens=1500, output_tokens=800, cost_usd=0.0234, duration_ms=2000)
    s = str(c.model_dump())
    assert "1,500" in s
    assert "800" in s
    assert "2,300" in s
    assert "$0.02340" in s
    assert "2.0s" in s


def test_trace_metrics_with_step_breakdown() -> None:
    rows = [
        {
            "step_id": "s1",
            "agent": "perceiver",
            "calls": 2,
            "tokens": 400,
            "cost_usd": 0.01,
            "duration_ms": 500,
        },
        {
            "step_id": "s2",
            "agent": "executor",
            "calls": 3,
            "tokens": 900,
            "cost_usd": 0.02,
            "duration_ms": 1100,
        },
    ]
    c = units.trace_metrics(step_breakdown=rows)
    s = str(c.model_dump())
    assert "s1" in s
    assert "s2" in s
    assert "perceiver" in s
    assert "executor" in s


# ── insight_body ────────────────────────────────────────────────────


def test_insight_body_renders_all_fields() -> None:
    d = InsightSurfaceData(
        signal_source="gmail",
        signal_category="security",
        signal_summary="Unusual login",
        relevance_score=0.87,
        relevance_reasoning="Matches pattern",
        related_goals=["Protect account"],
    )
    c = units.insight_body(d)
    s = str(c.model_dump())
    for token in (
        "Unusual login",
        "gmail",
        "security",
        "relevance: 0.87",
        "Matches pattern",
        "Protect account",
    ):
        assert token in s


# ── composite ───────────────────────────────────────────────────────


def test_build_run_surface_children_plan_ready() -> None:
    children = units.build_run_surface_children(
        title="My run",
        phase="plan_ready",
        agent_name="planner",
        progress="0/3",
        goal="Do a thing",
        reasoning="Because",
        success_criteria="Done",
        priority="high",
        trigger_type="user",
        steps=[
            StepState(step_id="s1", description="One", status="pending"),
            StepState(step_id="s2", description="Two", status="pending"),
        ],
        run_id="r1",
    )
    # header + plan + steps
    assert len(children) == 3


def test_build_run_surface_children_approval_needed_includes_approval() -> None:
    children = units.build_run_surface_children(
        title="Run",
        phase="approval_needed",
        steps=[StepState(step_id="s1", description="x", status="approval_needed")],
        approval=ApprovalContext(
            approval_id="apr",
            step_description="d",
            risk_reasoning="r",
            trust_context="t",
        ),
        run_id="r1",
    )
    # header + steps + approval (no plan or results)
    assert len(children) == 3


def test_build_run_surface_children_completed_includes_results_and_trace() -> None:
    children = units.build_run_surface_children(
        title="Run",
        phase="completed",
        steps=[StepState(step_id="s1", description="x", status="completed")],
        results=ResultSummary(key_findings=["ok"]),
        trace={"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001, "duration_ms": 800},
        run_id="r1",
    )
    # header + steps + results + trace
    assert len(children) == 4


def test_validate_surface_children_empty_raises() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        units.validate_surface_children([])


def test_validate_surface_children_non_component_raises() -> None:
    with pytest.raises(ValueError, match="not an A2UIComponent"):
        units.validate_surface_children([{"type": "Text"}])  # type: ignore[list-item]


# ── duration formatting ─────────────────────────────────────────────


def test_format_duration_scales() -> None:
    assert units._format_duration(None) == "—"
    assert units._format_duration(500) == "500ms"
    assert units._format_duration(2500) == "2.5s"
    assert units._format_duration(90_000) == "1.5m"
    assert units._format_duration(3_600_000) == "1.0h"
