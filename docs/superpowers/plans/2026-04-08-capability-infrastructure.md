# Spec 1A: Capability Infrastructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add capability abstraction infrastructure (models, MCP tool, resolver, routing function) alongside existing code — zero changes to existing behavior.

**Architecture:** Five additive components layered bottom-up: (1) Pydantic models in contracts.py, (2) capability summary generator, (3) `discover_capabilities` MCP tool registered in all 3 places, (4) `CapabilityResolver` service for capability→tool mapping, (5) `route_step()` function for future capability-based agent routing. Everything coexists with existing `PlannerOutput` and routing — no existing code is modified except additive appends.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy async, FastMCP, pytest

**Key conventions (from CLAUDE.md + existing code):**
- `ConfigDict(extra="ignore")` on all Pydantic models
- `Field(default_factory=...)` for mutable defaults
- Tool registration: `schemas.py` (input model) + `catalog.py` (InternalToolDef) + `intelligence_server.py` (handler) — all three
- Tests: class-based (`TestXxx`), exact count assertions, `make_mock_settings()` from conftest
- DB sessions: `async with db_factory() as db:` + `await db.commit()`
- ruff: line-length 100, target py312

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/orchestrator/contracts.py` | Modify (append) | Add `CapabilityGap`, `PlanStep`, `PlanOutput` models |
| `backend/src/tools/schemas.py` | Modify (append) | Add `DiscoverCapabilitiesInput` model + registry entry |
| `backend/src/tools/catalog.py` | Modify (append) | Add `discover_capabilities` InternalToolDef |
| `backend/src/tools/intelligence_server.py` | Modify (append) | Add `discover_capabilities` MCP handler |
| `backend/src/orchestrator/capability_summary.py` | Create | Capability summary generator |
| `backend/src/services/capability_resolver.py` | Create | CapabilityResolver + route_step() |
| `backend/tests/test_plan_output.py` | Create | Tests for PlanOutput, PlanStep, CapabilityGap |
| `backend/tests/test_capability_summary.py` | Create | Tests for generate_capability_summary() |
| `backend/tests/test_capability_resolver.py` | Create | Tests for CapabilityResolver + route_step() |
| `backend/tests/test_discover_capabilities.py` | Create | Tests for discover_capabilities MCP tool |

**Existing test files that need count updates (additive only):**
- `backend/tests/test_catalog.py` — internal tool count 22→23, intelligence server count 18→19
- `backend/tests/test_tool_schemas.py` — TOOL_INPUT_MODELS count 22→23, expected tool set

---

### Task 1: PlanStep, CapabilityGap, and PlanOutput Models

**Files:**
- Modify: `backend/src/orchestrator/contracts.py` (append after `WorkspaceSurfacePush` class, ~line 322)
- Create: `backend/tests/test_plan_output.py`

- [ ] **Step 1: Write failing tests for CapabilityGap**

Create `backend/tests/test_plan_output.py`:

```python
"""Tests for capability-based planning models (Spec 1A)."""

import pytest
from pydantic import ValidationError

from src.orchestrator.contracts import CapabilityGap, PlanOutput, PlanStep


# ── CapabilityGap ───────────────────────────────────────────────────


class TestCapabilityGap:
    def test_valid(self):
        g = CapabilityGap(
            description="Notion access needed",
            resolution="connect Notion",
            workaround="Manual copy-paste",
        )
        assert g.description == "Notion access needed"
        assert g.resolution == "connect Notion"
        assert g.workaround == "Manual copy-paste"

    def test_no_workaround(self):
        g = CapabilityGap(
            description="SMS not supported",
            resolution="not currently possible",
        )
        assert g.workaround is None

    def test_extra_ignored(self):
        g = CapabilityGap(
            description="x",
            resolution="y",
            unknown_field="z",
        )
        assert not hasattr(g, "unknown_field")

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            CapabilityGap(description="only desc")

    def test_model_dump_roundtrip(self):
        raw = {
            "description": "Calendar not connected",
            "resolution": "connect Google Calendar",
            "workaround": "Check calendar manually",
        }
        g = CapabilityGap.model_validate(raw)
        dumped = g.model_dump()
        assert dumped["description"] == "Calendar not connected"
        assert dumped["workaround"] == "Check calendar manually"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_plan_output.py::TestCapabilityGap -v`
Expected: FAIL with `ImportError: cannot import name 'CapabilityGap' from 'src.orchestrator.contracts'`

- [ ] **Step 3: Write failing tests for PlanStep**

Append to `backend/tests/test_plan_output.py`:

```python
# ── PlanStep ────────────────────────────────────────────────────────


class TestPlanStep:
    def test_valid_jarvis_step(self):
        s = PlanStep(
            step_id="step_1",
            description="Search emails for investor updates",
            capability="email.search",
            input={"query": "investor update", "max_results": 10},
        )
        assert s.step_id == "step_1"
        assert s.actor == "jarvis"
        assert s.capability == "email.search"
        assert s.risk == "none"
        assert s.depends_on == []

    def test_valid_user_step(self):
        s = PlanStep(
            step_id="step_2",
            description="Review the draft before sending",
            capability="none",
            actor="user",
            user_context="Please review the email draft and confirm",
        )
        assert s.actor == "user"
        assert s.user_context is not None

    def test_defaults(self):
        s = PlanStep(description="Think about it", capability="reason")
        assert s.step_id == ""
        assert s.actor == "jarvis"
        assert s.input == {}
        assert s.depends_on == []
        assert s.risk == "none"
        assert s.user_context is None

    def test_with_dependencies(self):
        s = PlanStep(
            step_id="step_3",
            description="Summarize findings",
            capability="reason",
            depends_on=["step_1", "step_2"],
        )
        assert s.depends_on == ["step_1", "step_2"]

    def test_risk_levels(self):
        for risk in ("none", "low", "medium", "high"):
            s = PlanStep(description="x", capability="y", risk=risk)
            assert s.risk == risk

    def test_invalid_risk_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="x", capability="y", risk="extreme")

    def test_invalid_actor_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="x", capability="y", actor="robot")

    def test_extra_ignored(self):
        s = PlanStep(
            description="x",
            capability="y",
            unknown_field="z",
        )
        assert not hasattr(s, "unknown_field")

    def test_missing_description_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(capability="email.search")

    def test_missing_capability_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(description="Do something")
```

- [ ] **Step 4: Run PlanStep tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_plan_output.py::TestPlanStep -v`
Expected: FAIL with `ImportError: cannot import name 'PlanStep' from 'src.orchestrator.contracts'`

- [ ] **Step 5: Write failing tests for PlanOutput**

Append to `backend/tests/test_plan_output.py`:

```python
# ── PlanOutput ──────────────────────────────────────────────────────


class TestPlanOutput:
    def test_valid_full_plan(self):
        p = PlanOutput(
            goal="Prepare for investor meeting",
            reasoning="Need to gather context and draft notes",
            steps=[
                PlanStep(
                    step_id="step_1",
                    description="Search emails",
                    capability="email.search",
                    input={"query": "investor meeting"},
                ),
                PlanStep(
                    step_id="step_2",
                    description="Check calendar",
                    capability="calendar.list",
                    depends_on=["step_1"],
                ),
                PlanStep(
                    step_id="step_3",
                    description="Draft summary",
                    capability="reason",
                    depends_on=["step_1", "step_2"],
                ),
            ],
            success_criteria="Summary of relevant emails and upcoming meeting details",
        )
        assert p.goal == "Prepare for investor meeting"
        assert len(p.steps) == 3
        assert p.achievable == "full"
        assert p.priority == "medium"
        assert p.requires_user_input is False

    def test_partial_achievability_with_gaps(self):
        p = PlanOutput(
            goal="Update Notion with meeting notes",
            achievable="partial",
            capability_gaps=[
                CapabilityGap(
                    description="Notion not connected",
                    resolution="connect Notion",
                    workaround="Copy to clipboard instead",
                ),
            ],
        )
        assert p.achievable == "partial"
        assert len(p.capability_gaps) == 1
        assert p.capability_gaps[0].resolution == "connect Notion"

    def test_not_achievable(self):
        p = PlanOutput(
            goal="Send an SMS",
            achievable="not_achievable",
            capability_gaps=[
                CapabilityGap(
                    description="SMS not supported",
                    resolution="not currently possible",
                ),
            ],
        )
        assert p.achievable == "not_achievable"

    def test_defaults(self):
        p = PlanOutput(goal="Simple task")
        assert p.reasoning == ""
        assert p.achievable == "full"
        assert p.priority == "medium"
        assert p.steps == []
        assert p.capability_gaps == []
        assert p.plan_id is None
        assert p.requires_user_input is False
        assert p.success_criteria == ""

    def test_requires_user_input_flag(self):
        p = PlanOutput(
            goal="Send email after review",
            requires_user_input=True,
            steps=[
                PlanStep(
                    step_id="step_1",
                    description="Draft email",
                    capability="email.draft",
                ),
                PlanStep(
                    step_id="step_2",
                    description="User reviews",
                    capability="none",
                    actor="user",
                    user_context="Review the draft",
                ),
            ],
        )
        assert p.requires_user_input is True

    def test_priority_levels(self):
        for pri in ("low", "medium", "high", "critical"):
            p = PlanOutput(goal="x", priority=pri)
            assert p.priority == pri

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput(goal="x", priority="ultra")

    def test_invalid_achievable_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput(goal="x", achievable="maybe")

    def test_extra_ignored(self):
        p = PlanOutput(goal="x", unknown_field="z")
        assert not hasattr(p, "unknown_field")

    def test_missing_goal_rejected(self):
        with pytest.raises(ValidationError):
            PlanOutput()

    def test_with_plan_id(self):
        p = PlanOutput(goal="x", plan_id="plan_01ABC")
        assert p.plan_id == "plan_01ABC"

    def test_model_dump_roundtrip(self):
        raw = {
            "goal": "Draft reply to CEO",
            "reasoning": "Urgent request",
            "priority": "high",
            "steps": [
                {
                    "step_id": "step_1",
                    "description": "Read email",
                    "capability": "email.read",
                    "input": {"message_id": "msg_123"},
                },
                {
                    "step_id": "step_2",
                    "description": "Draft reply",
                    "capability": "email.draft",
                    "depends_on": ["step_1"],
                    "risk": "medium",
                },
            ],
            "success_criteria": "Draft ready for review",
        }
        p = PlanOutput.model_validate(raw)
        dumped = p.model_dump()
        assert dumped["goal"] == "Draft reply to CEO"
        assert len(dumped["steps"]) == 2
        assert dumped["steps"][1]["depends_on"] == ["step_1"]

    def test_model_validate_from_claude_json(self):
        """Simulate raw JSON from Claude API response."""
        raw = {
            "goal": "Find my last email from Alice",
            "reasoning": "Simple search task",
            "achievable": "full",
            "priority": "low",
            "steps": [
                {
                    "step_id": "step_1",
                    "description": "Search emails from Alice",
                    "capability": "email.search",
                    "input": {"query": "from:alice"},
                }
            ],
            "success_criteria": "Email found and displayed",
            "capability_gaps": [],
            "extra_field_from_llm": "should be ignored",
        }
        p = PlanOutput.model_validate(raw)
        assert p.goal == "Find my last email from Alice"
        assert len(p.steps) == 1
        assert not hasattr(p, "extra_field_from_llm")
```

- [ ] **Step 6: Run all PlanOutput tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_plan_output.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 7: Implement the three models in contracts.py**

Append to `backend/src/orchestrator/contracts.py` after the `WorkspaceSurfacePush` class (after line 322):

```python
# ── Capability-based planning contracts (Spec 1A) ──────────────────


class CapabilityGap(BaseModel):
    """A capability the plan needs but doesn't have."""

    model_config = ConfigDict(extra="ignore")

    description: str
    resolution: str  # e.g. "connect Notion" or "not currently possible"
    workaround: str | None = None


class PlanStep(BaseModel):
    """A single step in a capability-based plan."""

    model_config = ConfigDict(extra="ignore")

    step_id: str = ""
    description: str
    actor: Literal["jarvis", "user"] = "jarvis"
    capability: str  # e.g. "email.search", "reason", "respond"
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    risk: Literal["none", "low", "medium", "high"] = "none"
    user_context: str | None = None


class PlanOutput(BaseModel):
    """Validated planner output — a goal-decomposed plan."""

    model_config = ConfigDict(extra="ignore")

    goal: str
    reasoning: str = ""
    achievable: Literal["full", "partial", "not_achievable"] = "full"
    priority: Literal["low", "medium", "high", "critical"] = "medium"

    steps: list[PlanStep] = Field(default_factory=list)
    success_criteria: str = ""

    capability_gaps: list[CapabilityGap] = Field(default_factory=list)

    plan_id: str | None = None
    requires_user_input: bool = False
```

Note: `CapabilityGap` must be defined **before** `PlanOutput` because `PlanOutput.capability_gaps` references it. Order matters for forward references in Pydantic v2 with `from __future__ import annotations` — but since contracts.py already uses that import, this order ensures clarity.

- [ ] **Step 8: Run all tests to verify they pass**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_plan_output.py -v`
Expected: All 25 tests PASS

- [ ] **Step 9: Verify no existing tests broke**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_contracts.py tests/test_contracts_v2.py -v`
Expected: All existing contract tests PASS (we only appended, nothing changed)

- [ ] **Step 10: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/orchestrator/contracts.py tests/test_plan_output.py
git commit -m "feat(spec1a): add PlanOutput, PlanStep, CapabilityGap models"
```

---

### Task 2: Capability Summary Generator

**Files:**
- Create: `backend/src/orchestrator/capability_summary.py`
- Create: `backend/tests/test_capability_summary.py`

**Context:** This component generates a compact (~200 token) capability summary from the tool registry and installation status. It queries `ToolDefinition` records grouped by capability family and cross-references `IntegrationInstallation` for connection status. The Planner will eventually receive this instead of 15-20K tokens of raw tool schemas.

**Key data flow:**
- `ToolRegistry.list_tools(enabled_only=True)` returns `ToolDefinition` rows with `.capability` (e.g. `"email.search"`)
- `IntegrationInstallation` table has `server_name` (e.g. `"google-workspace"`) and `status` (active/paused/error/disabled) and `enabled`
- We group tools by capability family prefix (e.g. `"email"` from `"email.search"`) and check if the corresponding server has an active installation
- Servers without installations are "disconnected" (available but not connected)

**Family display names mapping** (derived from `CapabilityFamily` in `capabilities.py` and `EXTERNAL_TOOL_SEEDS` server names):

| Family prefix | Server name | Display name |
|---|---|---|
| email | google-workspace | Email (Gmail) |
| calendar | google-workspace | Calendar (Google) |
| repo | github | Code (GitHub) |
| issue | github, linear, atlassian | Issues (GitHub/Linear/Jira) |
| doc | notion | Documents (Notion) |
| workflow | linear | Projects (Linear) |
| messaging | slack | Messaging (Slack) |
| browser | playwright | Browser |
| search | _composite, github | Search |
| filesystem | filesystem | Files |
| internal | (internal) | Internal |

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_capability_summary.py`:

```python
"""Tests for capability summary generator (Spec 1A)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.capability_summary import (
    _family_display_name,
    _group_by_family,
    generate_capability_summary,
)


class TestFamilyDisplayName:
    def test_email(self):
        assert _family_display_name("email") == "Email (Gmail)"

    def test_calendar(self):
        assert _family_display_name("calendar") == "Calendar (Google)"

    def test_repo(self):
        assert _family_display_name("repo") == "Code (GitHub)"

    def test_issue(self):
        assert _family_display_name("issue") == "Issues"

    def test_doc(self):
        assert _family_display_name("doc") == "Documents (Notion)"

    def test_workflow(self):
        assert _family_display_name("workflow") == "Projects (Linear)"

    def test_messaging(self):
        assert _family_display_name("messaging") == "Messaging (Slack)"

    def test_browser(self):
        assert _family_display_name("browser") == "Browser"

    def test_search(self):
        assert _family_display_name("search") == "Search"

    def test_filesystem(self):
        assert _family_display_name("filesystem") == "Files"

    def test_internal(self):
        assert _family_display_name("internal") == "Internal"

    def test_unknown_passes_through(self):
        assert _family_display_name("some_new_thing") == "some_new_thing"


class TestGroupByFamily:
    def test_groups_capabilities(self):
        tools = [
            _mock_tool("search_gmail_messages", "email.search"),
            _mock_tool("get_gmail_message_content", "email.read"),
            _mock_tool("send_gmail_message", "email.send"),
            _mock_tool("get_events", "calendar.list"),
        ]
        families = _group_by_family(tools)
        assert sorted(families["email"]) == ["read", "search", "send"]
        assert families["calendar"] == ["list"]

    def test_skips_tools_without_capability(self):
        tools = [
            _mock_tool("mystery_tool", None),
            _mock_tool("search_gmail", "email.search"),
        ]
        families = _group_by_family(tools)
        assert "email" in families
        assert len(families) == 1

    def test_deduplicates_actions(self):
        tools = [
            _mock_tool("tool_a", "email.read"),
            _mock_tool("tool_b", "email.read"),
        ]
        families = _group_by_family(tools)
        assert families["email"] == ["read"]

    def test_empty_tools(self):
        assert _group_by_family([]) == {}


class TestGenerateCapabilitySummary:
    @pytest.mark.asyncio
    async def test_connected_services(self):
        """Summary includes connected services from tool registry."""
        tools = [
            _mock_tool("search_gmail", "email.search"),
            _mock_tool("send_gmail", "email.send"),
            _mock_tool("get_events", "calendar.list"),
        ]
        installations = [
            _mock_installation("google-workspace", enabled=True, status="active"),
        ]
        summary = await _generate_with_mocks(tools, installations, "ws_test")

        assert "<connected_services>" in summary
        assert "Email (Gmail)" in summary
        assert "search, send" in summary
        assert "Calendar (Google)" in summary

    @pytest.mark.asyncio
    async def test_disconnected_services(self):
        """Services with seeds but no installation are disconnected."""
        tools = [
            _mock_tool("search_gmail", "email.search"),
        ]
        installations = []  # No installations at all
        # Seed servers that have no installation
        seed_servers = ["slack", "notion"]

        summary = await _generate_with_mocks(
            tools, installations, "ws_test", seed_servers=seed_servers,
        )
        assert "<disconnected_services>" in summary

    @pytest.mark.asyncio
    async def test_empty_workspace(self):
        """Empty workspace with no tools produces minimal output."""
        summary = await _generate_with_mocks([], [], "ws_test")
        assert "<connected_services>" in summary
        assert "</connected_services>" in summary

    @pytest.mark.asyncio
    async def test_internal_tools_excluded(self):
        """Internal tools (internal.*) are excluded from the summary."""
        tools = [
            _mock_tool("search", "internal.search"),
            _mock_tool("search_gmail", "email.search"),
        ]
        summary = await _generate_with_mocks(tools, [], "ws_test")
        assert "Internal" not in summary
        assert "Email (Gmail)" in summary


# ── Test helpers ────────────────────────────────────────────────────


def _mock_tool(name: str, capability: str | None) -> MagicMock:
    """Create a mock ToolDefinition."""
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.description = f"Mock {name}"
    tool.risk_level = "low"
    tool.requires_approval = False
    tool.server = "google-workspace"
    tool.enabled = True
    return tool


def _mock_installation(
    server_name: str,
    enabled: bool = True,
    status: str = "active",
) -> MagicMock:
    """Create a mock IntegrationInstallation."""
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = server_name
    inst.enabled = enabled
    inst.status = status
    return inst


async def _generate_with_mocks(
    tools: list,
    installations: list,
    workspace_id: str,
    seed_servers: list[str] | None = None,
) -> str:
    """Run generate_capability_summary with mocked DB queries."""
    mock_db = AsyncMock()

    # First query: ToolRegistry.list_tools → tools
    # Second query: IntegrationInstallation → installations
    tool_result = MagicMock()
    tool_result.scalars.return_value.all.return_value = tools

    inst_result = MagicMock()
    inst_result.scalars.return_value.all.return_value = installations

    mock_db.execute = AsyncMock(side_effect=[tool_result, inst_result])

    with patch(
        "src.orchestrator.capability_summary._get_seed_server_names",
        return_value=seed_servers or [],
    ):
        return await generate_capability_summary(mock_db, workspace_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_capability_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.orchestrator.capability_summary'`

- [ ] **Step 3: Implement capability_summary.py**

Create `backend/src/orchestrator/capability_summary.py`:

```python
"""Capability summary generator — compact capability view for Planner prompts.

Replaces ~15-20K tokens of raw tool schemas with a ~200 token summary
of connected and disconnected services grouped by capability family.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.integration_installation import IntegrationInstallation
from src.models.tool_definitions import ToolDefinition
from src.tools.catalog import EXTERNAL_TOOL_SEEDS

# ── Family display names ───────────────────────────────────────────

_FAMILY_DISPLAY: dict[str, str] = {
    "email": "Email (Gmail)",
    "calendar": "Calendar (Google)",
    "repo": "Code (GitHub)",
    "issue": "Issues",
    "doc": "Documents (Notion)",
    "workflow": "Projects (Linear)",
    "messaging": "Messaging (Slack)",
    "browser": "Browser",
    "search": "Search",
    "filesystem": "Files",
    "internal": "Internal",
}


def _family_display_name(family: str) -> str:
    """Map capability family prefix to a human-readable display name."""
    return _FAMILY_DISPLAY.get(family, family)


def _group_by_family(tools: list) -> dict[str, list[str]]:
    """Group tools by capability family, collecting unique action names.

    Input: list of ToolDefinition-like objects with .capability attribute.
    Output: {"email": ["search", "read", "send"], "calendar": ["list"]}
    """
    families: dict[str, list[str]] = {}
    for tool in tools:
        if not tool.capability:
            continue
        parts = tool.capability.split(".")
        family = parts[0]
        action = parts[-1] if len(parts) > 1 else tool.capability
        if family not in families:
            families[family] = []
        if action not in families[family]:
            families[family].append(action)
    return families


def _get_seed_server_names() -> list[str]:
    """Get unique server names from external tool seeds."""
    servers = set()
    for seed in EXTERNAL_TOOL_SEEDS:
        if not seed.server.startswith("_"):
            servers.add(seed.server)
    return sorted(servers)


async def generate_capability_summary(
    db: AsyncSession,
    workspace_id: str,
) -> str:
    """Generate a compact capability summary for the Planner prompt.

    Queries tool_definitions grouped by capability family,
    cross-references with installations for connection status.
    Returns XML-formatted summary of connected/disconnected services.
    """
    # Query enabled tools
    stmt = select(ToolDefinition).where(ToolDefinition.enabled.is_(True))
    result = await db.execute(stmt)
    all_tools = list(result.scalars().all())

    # Query active installations for this workspace
    inst_stmt = (
        select(IntegrationInstallation)
        .where(IntegrationInstallation.workspace_id == workspace_id)
        .where(IntegrationInstallation.enabled.is_(True))
        .where(IntegrationInstallation.status == "active")
    )
    inst_result = await db.execute(inst_stmt)
    installations = list(inst_result.scalars().all())
    connected_servers = {inst.server_name for inst in installations}

    # Group by family, excluding internal tools
    families = _group_by_family(
        [t for t in all_tools if t.capability and not t.capability.startswith("internal.")]
    )

    # Build connected services section
    lines = ["<connected_services>"]
    for family, actions in sorted(families.items()):
        display = _family_display_name(family)
        lines.append(f"- {display}: {', '.join(sorted(actions))}")
    lines.append("</connected_services>")

    # Find disconnected services (seed servers with no active installation)
    seed_servers = _get_seed_server_names()
    disconnected = [s for s in seed_servers if s not in connected_servers]
    if disconnected:
        lines.append("")
        lines.append("<disconnected_services>")
        for name in disconnected:
            lines.append(f"- {name}: available but not connected")
        lines.append("</disconnected_services>")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_capability_summary.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/orchestrator/capability_summary.py tests/test_capability_summary.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/orchestrator/capability_summary.py tests/test_capability_summary.py
git commit -m "feat(spec1a): add capability summary generator"
```

---

### Task 3: discover_capabilities MCP Tool

**Files:**
- Modify: `backend/src/tools/schemas.py` (append input model + registry entry)
- Modify: `backend/src/tools/catalog.py` (append InternalToolDef + update import)
- Modify: `backend/src/tools/intelligence_server.py` (append handler)
- Create: `backend/tests/test_discover_capabilities.py`
- Modify: `backend/tests/test_catalog.py` (update counts: 22→23 internal tools, 18→19 intelligence)
- Modify: `backend/tests/test_tool_schemas.py` (update count: 22→23, add to expected set)

**Context:** Internal MCP tools must be registered in all 3 places:
1. `schemas.py` — Pydantic input model + entry in `TOOL_INPUT_MODELS` dict
2. `catalog.py` — `InternalToolDef` in `INTERNAL_TOOLS` list + import of the schema
3. `intelligence_server.py` — `@intelligence.tool()` handler function

- [ ] **Step 1: Write failing tests for the tool**

Create `backend/tests/test_discover_capabilities.py`:

```python
"""Tests for discover_capabilities MCP tool (Spec 1A)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.schemas import DiscoverCapabilitiesInput


class TestDiscoverCapabilitiesInput:
    def test_valid(self):
        inp = DiscoverCapabilitiesInput(query="email")
        assert inp.query == "email"

    def test_has_docstring(self):
        assert DiscoverCapabilitiesInput.__doc__
        assert len(DiscoverCapabilitiesInput.__doc__.strip()) > 10

    def test_schema_has_query_field(self):
        schema = DiscoverCapabilitiesInput.model_json_schema()
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"


class TestDiscoverCapabilitiesRegistration:
    def test_in_tool_input_models(self):
        from src.tools.schemas import TOOL_INPUT_MODELS

        assert "discover_capabilities" in TOOL_INPUT_MODELS
        assert TOOL_INPUT_MODELS["discover_capabilities"] is DiscoverCapabilitiesInput

    def test_in_internal_tools_catalog(self):
        from src.tools.catalog import get_internal_tool_by_name

        tool = get_internal_tool_by_name("discover_capabilities")
        assert tool is not None
        assert tool.capability == "system.discovery"
        assert tool.risk_level == "none"
        assert tool.requires_approval is False
        assert tool.server == "intelligence"
        assert tool.read_only is True

    def test_input_model_matches_catalog(self):
        from src.tools.catalog import get_internal_tool_by_name

        tool = get_internal_tool_by_name("discover_capabilities")
        assert tool is not None
        assert tool.input_model is DiscoverCapabilitiesInput


class TestDiscoverCapabilitiesHandler:
    @pytest.mark.asyncio
    async def test_returns_matching_capabilities(self):
        """Handler returns capabilities matching the query."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("search_gmail_messages", "email.search", "Search Gmail"),
            _mock_tool("get_gmail_message_content", "email.read", "Read Gmail"),
            _mock_tool("send_gmail_message", "email.send", "Send Gmail"),
            _mock_tool("get_events", "calendar.list", "List events"),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="email", ctx=ctx
            )
            assert "capabilities" in result
            caps = result["capabilities"]
            assert len(caps) == 3
            cap_names = {c["capability"] for c in caps}
            assert cap_names == {"email.search", "email.read", "email.send"}
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_no_matches(self):
        """Handler returns empty list when no capabilities match."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("search_gmail", "email.search", "Search Gmail"),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="notion", ctx=ctx
            )
            assert result["capabilities"] == []
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_matches_description(self):
        """Handler matches against tool descriptions too."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("get_events", "calendar.list", "List upcoming calendar events"),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="upcoming", ctx=ctx
            )
            caps = result["capabilities"]
            assert len(caps) == 1
            assert caps[0]["capability"] == "calendar.list"
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_deduplicates_capabilities(self):
        """Same capability from multiple tools appears only once."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("tool_a", "email.read", "Read email A"),
            _mock_tool("tool_b", "email.read", "Read email B"),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="email", ctx=ctx
            )
            caps = result["capabilities"]
            assert len(caps) == 1
            assert caps[0]["capability"] == "email.read"
            assert len(caps[0]["tools"]) == 2
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_skips_tools_without_capability(self):
        """Tools with no capability are excluded."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("mystery", None, "Unknown tool"),
            _mock_tool("search_gmail", "email.search", "Search Gmail"),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="email", ctx=ctx
            )
            assert len(result["capabilities"]) == 1
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_includes_risk_and_status(self):
        """Each capability entry includes risk level and status."""
        from src.tools import intelligence_server

        tools = [
            _mock_tool("send_gmail", "email.send", "Send email", risk="high", approval=True),
        ]

        mock_db, cleanup = _configure_with_tools(tools)
        try:
            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(
                query="email", ctx=ctx
            )
            cap = result["capabilities"][0]
            assert cap["risk"] == "high"
            assert cap["status"] == "connected"
            assert cap["description"] == "Send email"
        finally:
            cleanup()


# ── Test helpers ────────────────────────────────────────────────────


def _mock_tool(
    name: str,
    capability: str | None,
    description: str = "",
    risk: str = "low",
    approval: bool = False,
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.description = description
    tool.risk_level = risk
    tool.requires_approval = approval
    tool.enabled = True
    return tool


def _mock_ctx() -> AsyncMock:
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.error = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.debug = AsyncMock()
    ctx.report_progress = AsyncMock()
    return ctx


def _configure_with_tools(tools: list) -> tuple[AsyncMock, callable]:
    """Configure intelligence_server with mocked DB returning given tools.

    Returns (mock_db, cleanup_fn). Call cleanup_fn in finally block.
    """
    from src.tools import intelligence_server

    # Save original state
    old_db_factory = intelligence_server._db_factory
    old_settings = intelligence_server._settings
    old_services = intelligence_server._services

    mock_session = AsyncMock()
    tool_result = MagicMock()
    tool_result.scalars.return_value.all.return_value = tools
    mock_session.execute = AsyncMock(return_value=tool_result)

    mock_db_factory = MagicMock()
    async_cm = AsyncMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_session)
    async_cm.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory.return_value = async_cm

    intelligence_server.configure(mock_db_factory, MagicMock(), MagicMock())

    def cleanup():
        intelligence_server._db_factory = old_db_factory
        intelligence_server._settings = old_settings
        intelligence_server._services = old_services

    return mock_session, cleanup
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_discover_capabilities.py -v`
Expected: FAIL with `ImportError: cannot import name 'DiscoverCapabilitiesInput' from 'src.tools.schemas'`

- [ ] **Step 3: Add DiscoverCapabilitiesInput to schemas.py**

Append to `backend/src/tools/schemas.py` before the `TOOL_INPUT_MODELS` dict (before line 252):

```python
class DiscoverCapabilitiesInput(BaseModel):
    """Search available capabilities by query. Returns matching capabilities with descriptions, tools, risk levels, and connection status."""

    query: str = Field(description="Search query, e.g. 'email', 'calendar management'")
```

Then add to the `TOOL_INPUT_MODELS` dict:

```python
    "discover_capabilities": DiscoverCapabilitiesInput,
```

- [ ] **Step 4: Add InternalToolDef to catalog.py**

First, add import of `DiscoverCapabilitiesInput` to the existing import block at top of `catalog.py` (line 19-42).

Then append to `INTERNAL_TOOLS` list (before the `# Communication server tools` comment, after the `get_plan_details` entry):

```python
    InternalToolDef(
        name="discover_capabilities",
        input_model=DiscoverCapabilitiesInput,
        capability="system.discovery",
        risk_level="none",
        requires_approval=False,
        server="intelligence",
        description=_desc(DiscoverCapabilitiesInput),
        read_only=True,
    ),
```

Note: `capability="system.discovery"` uses a new prefix `system.` — this is intentional. The `internal.*` prefix is for backend housekeeping tools. `system.discovery` is a meta-tool for the Planner to introspect the system's own capabilities. This does NOT need an entry in `CAPABILITY_CATALOG` (capabilities.py) because it's a system-level tool, not a user-facing capability family.

- [ ] **Step 5: Add handler to intelligence_server.py**

Append to `backend/src/tools/intelligence_server.py` (at the end of the file):

```python
# ── Capability Discovery ────────────────────────────────────────────────


@intelligence.tool(
    tags={"planner", "read"},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def discover_capabilities(
    query: str,
    ctx: Context,
) -> dict:
    """Search available capabilities by query. Returns matching capabilities with descriptions, tools, risk levels, and connection status."""
    async with _get_db() as db:
        stmt = select(ToolDefinition).where(ToolDefinition.enabled.is_(True))
        result = await db.execute(stmt)
        all_tools = list(result.scalars().all())

    matches: list[dict] = []
    query_lower = query.lower()
    seen_capabilities: set[str] = set()

    for tool in all_tools:
        if not tool.capability:
            continue
        cap = tool.capability
        desc = tool.description or ""
        if query_lower not in cap.lower() and query_lower not in desc.lower():
            continue
        if cap in seen_capabilities:
            # Add this tool to existing capability entry
            for m in matches:
                if m["capability"] == cap:
                    m["tools"].append(tool.name)
                    break
            continue

        seen_capabilities.add(cap)
        matches.append({
            "capability": cap,
            "tools": [tool.name],
            "risk": tool.risk_level or "none",
            "status": "connected",
            "description": desc,
        })

    return {"capabilities": matches}
```

Also add this import at the top of intelligence_server.py (with the existing imports from src.models):

```python
from src.models.tool_definitions import ToolDefinition
```

- [ ] **Step 6: Run new tool tests**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_discover_capabilities.py -v`
Expected: All 10 tests PASS

- [ ] **Step 7: Update existing test counts in test_catalog.py**

In `backend/tests/test_catalog.py`:

Update `test_internal_tools_count` (line 17):
```python
# Old:
    assert len(INTERNAL_TOOLS) == 22
# New:
    assert len(INTERNAL_TOOLS) == 23
```

Update `test_internal_tool_names_match_jarvis` — add `"discover_capabilities"` to the `expected_names` set (line 25-48).

Update `test_server_distribution` (line 63):
```python
# Old:
    assert server_counts.get("intelligence", 0) == 18, "Expected 18 intelligence tools"
# New:
    assert server_counts.get("intelligence", 0) == 19, "Expected 19 intelligence tools"
```

- [ ] **Step 8: Update existing test counts in test_tool_schemas.py**

In `backend/tests/test_tool_schemas.py`:

Update `test_tool_count_is_21` (line 13-19):
```python
# Old:
        assert len(TOOL_INPUT_MODELS) == 22, (
            f"Expected 22 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
        )
# New:
        assert len(TOOL_INPUT_MODELS) == 23, (
            f"Expected 23 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
        )
```

Update `test_expected_tools_present` — add `"discover_capabilities"` to the `expected` set (line 45-68).

- [ ] **Step 9: Run all affected tests**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_discover_capabilities.py tests/test_catalog.py tests/test_tool_schemas.py -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py \
  tests/test_discover_capabilities.py tests/test_catalog.py tests/test_tool_schemas.py
git commit -m "feat(spec1a): add discover_capabilities MCP tool"
```

---

### Task 4: CapabilityResolver Service

**Files:**
- Create: `backend/src/services/capability_resolver.py`
- Create: `backend/tests/test_capability_resolver.py`

**Context:** The resolver maps capability strings (e.g. `"email.search"`) to specific `ToolDefinition` records and produces Claude API tool definitions. It also includes related read tools from the same family for context (read-before-write principle). The `route_step()` function maps capabilities to agent names for future use in Spec 1B.

**Key design decisions:**
- `resolve()` returns raw `ToolDefinition` objects for flexibility
- `resolve_for_step()` builds Claude API tool dicts (name + description + input_schema) — includes primary tools + related read tools from the same family
- `is_read_capability()` / `is_write_capability()` use `requires_approval` as the read/write signal (same as `ToolRegistry.is_write_tool()`)
- `route_step()` returns agent name strings: `"presenter"`, `"perceiver"`, `"operator"`, `"librarian"` — "perceiver" is a future agent (Spec 1B-i)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_capability_resolver.py`:

```python
"""Tests for CapabilityResolver and route_step (Spec 1A)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.capability_resolver import CapabilityResolver, route_step


# ── Test helpers ────────────────────────────────────────────────────


def _mock_tool(
    name: str,
    capability: str,
    requires_approval: bool = False,
    description: str = "",
    input_schema: dict | None = None,
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.requires_approval = requires_approval
    tool.description = description or f"Mock {name}"
    tool.risk_level = "high" if requires_approval else "low"
    tool.input_schema = input_schema or {"type": "object"}
    tool.enabled = True
    return tool


def _mock_db_with_tools(tools: list) -> AsyncMock:
    """Create a mock DB session that returns given tools for any SELECT."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = tools
    db.execute = AsyncMock(return_value=result)
    return db


# ── CapabilityResolver.resolve() ────────────────────────────────────


class TestResolve:
    @pytest.mark.asyncio
    async def test_single_match(self):
        tools = [
            _mock_tool("search_gmail_messages", "email.search"),
            _mock_tool("get_events", "calendar.list"),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve("email.search")
        assert len(result) == 1
        assert result[0].name == "search_gmail_messages"

    @pytest.mark.asyncio
    async def test_multiple_matches(self):
        tools = [
            _mock_tool("get_gmail_message_content", "email.read"),
            _mock_tool("get_gmail_messages_content_batch", "email.read"),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve("email.read")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_match(self):
        tools = [
            _mock_tool("search_gmail", "email.search"),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve("notion.create")
        assert result == []

    @pytest.mark.asyncio
    async def test_unknown_capability(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve("alien.teleport")
        assert result == []


# ── CapabilityResolver.resolve_for_step() ───────────────────────────


class TestResolveForStep:
    @pytest.mark.asyncio
    async def test_primary_tools_included(self):
        tools = [
            _mock_tool(
                "send_gmail_message",
                "email.send",
                requires_approval=True,
                description="Send Gmail message",
                input_schema={"type": "object", "properties": {"to": {"type": "string"}}},
            ),
            _mock_tool(
                "search_gmail_messages",
                "email.search",
                description="Search Gmail",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve_for_step("email.send")
        names = {t["name"] for t in result}
        assert "send_gmail_message" in names

    @pytest.mark.asyncio
    async def test_related_read_tools_included(self):
        """Write capability also includes read tools from same family."""
        tools = [
            _mock_tool("send_gmail_message", "email.send", requires_approval=True),
            _mock_tool("search_gmail_messages", "email.search"),
            _mock_tool("get_gmail_message_content", "email.read"),
            _mock_tool("get_events", "calendar.list"),  # Different family
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve_for_step("email.send")
        names = {t["name"] for t in result}
        assert "send_gmail_message" in names
        assert "search_gmail_messages" in names
        assert "get_gmail_message_content" in names
        assert "get_events" not in names  # Different family

    @pytest.mark.asyncio
    async def test_returns_claude_api_format(self):
        tools = [
            _mock_tool(
                "search_gmail_messages",
                "email.search",
                description="Search Gmail messages",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve_for_step("email.search")
        assert len(result) == 1
        tool_def = result[0]
        assert "name" in tool_def
        assert "description" in tool_def
        assert "input_schema" in tool_def
        assert tool_def["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_no_capability_match(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")

        result = await resolver.resolve_for_step("nonexistent.capability")
        assert result == []


# ── CapabilityResolver.is_read_capability() / is_write_capability() ──


class TestReadWriteCapability:
    @pytest.mark.asyncio
    async def test_read_only_tools(self):
        tools = [
            _mock_tool("search_gmail", "email.search", requires_approval=False),
            _mock_tool("get_gmail_content", "email.search", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        assert await resolver.is_read_capability("email.search") is True
        assert await resolver.is_write_capability("email.search") is False

    @pytest.mark.asyncio
    async def test_write_tools(self):
        tools = [
            _mock_tool("send_gmail", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        assert await resolver.is_write_capability("email.send") is True
        assert await resolver.is_read_capability("email.send") is False

    @pytest.mark.asyncio
    async def test_mixed_approval(self):
        """If any tool requires approval, the capability is write."""
        tools = [
            _mock_tool("tool_a", "email.send", requires_approval=False),
            _mock_tool("tool_b", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")

        assert await resolver.is_write_capability("email.send") is True
        assert await resolver.is_read_capability("email.send") is False

    @pytest.mark.asyncio
    async def test_unknown_capability(self):
        """Unknown capability is neither read nor write (no tools found)."""
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")

        # No tools → all() returns True on empty → is_read = True
        assert await resolver.is_read_capability("unknown.cap") is True
        assert await resolver.is_write_capability("unknown.cap") is False


# ── route_step() ────────────────────────────────────────────────────


class TestRouteStep:
    @pytest.mark.asyncio
    async def test_reason_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("reason", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_respond_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("respond", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_none_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("none", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_knowledge_routes_to_librarian(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("knowledge.store", resolver) == "librarian"

    @pytest.mark.asyncio
    async def test_knowledge_search_routes_to_librarian(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("knowledge.search", resolver) == "librarian"

    @pytest.mark.asyncio
    async def test_read_capability_routes_to_perceiver(self):
        tools = [
            _mock_tool("search_gmail", "email.search", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("email.search", resolver) == "perceiver"

    @pytest.mark.asyncio
    async def test_write_capability_routes_to_operator(self):
        tools = [
            _mock_tool("send_gmail", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("email.send", resolver) == "operator"

    @pytest.mark.asyncio
    async def test_unknown_capability_routes_to_operator(self):
        """Unknown capability (no tools found) falls back to operator."""
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db, "ws_test")
        assert await route_step("alien.teleport", resolver) == "operator"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_capability_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.capability_resolver'`

- [ ] **Step 3: Implement capability_resolver.py**

Create `backend/src/services/capability_resolver.py`:

```python
"""Capability resolver — maps capability references to specific tools.

Given a capability like "email.search", finds the tool(s) that provide it
and returns Claude API tool definitions. Also includes route_step() for
capability-based agent routing (wired in Spec 1B).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tool_definitions import ToolDefinition


class CapabilityResolver:
    """Resolves capability references to specific tools.

    Given a capability like "email.search", finds the tool(s) that
    provide it and returns Claude API tool definitions.
    """

    def __init__(self, db: AsyncSession, workspace_id: str = ""):
        self._db = db
        self._workspace_id = workspace_id

    async def _list_enabled_tools(self) -> list[ToolDefinition]:
        """Fetch all enabled tools from the registry."""
        stmt = select(ToolDefinition).where(ToolDefinition.enabled.is_(True))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def resolve(self, capability: str) -> list[ToolDefinition]:
        """Find tools that provide a given capability."""
        all_tools = await self._list_enabled_tools()
        return [t for t in all_tools if t.capability == capability]

    async def resolve_for_step(self, step_capability: str) -> list[dict]:
        """Build Claude API tool definitions for a plan step.

        Returns tools for the step's capability PLUS related read tools
        from the same family (Operator may need to read context before writing).
        """
        all_tools = await self._list_enabled_tools()
        primary = [t for t in all_tools if t.capability == step_capability]

        # Also include read tools from same family
        family = step_capability.split(".")[0] if "." in step_capability else ""
        related_read = []
        if family:
            for t in all_tools:
                if (
                    t.capability
                    and t.capability.startswith(f"{family}.")
                    and not t.requires_approval
                    and t.capability != step_capability
                ):
                    related_read.append(t)

        # Convert to Claude API tool format
        tools: list[dict] = []
        seen_names: set[str] = set()
        for tool_def in primary + related_read:
            if tool_def.name in seen_names:
                continue
            seen_names.add(tool_def.name)
            tools.append({
                "name": tool_def.name,
                "description": tool_def.description or tool_def.name,
                "input_schema": tool_def.input_schema or {"type": "object"},
            })

        return tools

    async def is_read_capability(self, capability: str) -> bool:
        """Check if a capability is read-only (no tool requires approval)."""
        tools = await self.resolve(capability)
        return all(not t.requires_approval for t in tools)

    async def is_write_capability(self, capability: str) -> bool:
        """Check if a capability involves writes (any tool requires approval)."""
        tools = await self.resolve(capability)
        return any(t.requires_approval for t in tools)


async def route_step(step_capability: str, resolver: CapabilityResolver) -> str:
    """Route a plan step to the appropriate agent based on capability.

    Returns agent name: "presenter", "perceiver", "operator", "librarian".
    Note: "perceiver" is a future agent (Observer + Researcher merge, Spec 1B-i).
    This function is built now but NOT wired into the orchestrator until Spec 1B.
    """
    # Pure reasoning / response — no tools needed
    if step_capability in ("reason", "respond", "none"):
        return "presenter"

    # Memory / knowledge operations
    if step_capability.startswith("knowledge."):
        return "librarian"

    # Check if read-only or write capability
    if await resolver.is_read_capability(step_capability):
        return "perceiver"

    if await resolver.is_write_capability(step_capability):
        return "operator"

    # Unknown capability — Operator as fallback
    return "operator"
```

- [ ] **Step 4: Run all resolver tests**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_capability_resolver.py -v`
Expected: All 18 tests PASS

- [ ] **Step 5: Lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/services/capability_resolver.py tests/test_capability_resolver.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/capability_resolver.py tests/test_capability_resolver.py
git commit -m "feat(spec1a): add CapabilityResolver and route_step"
```

---

### Task 5: Full Integration Verification

**Files:**
- No new files — run existing + new tests together

- [ ] **Step 1: Run all new Spec 1A tests together**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_plan_output.py tests/test_capability_summary.py tests/test_discover_capabilities.py tests/test_capability_resolver.py -v`
Expected: All ~65 tests PASS

- [ ] **Step 2: Run all existing tests to verify no regressions**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/ -v --timeout=60`
Expected: All ~1137+ tests PASS (the total is now higher by ~65 new tests)

- [ ] **Step 3: Run linter on all changed files**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/orchestrator/contracts.py src/orchestrator/capability_summary.py src/services/capability_resolver.py src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py`
Expected: No errors

- [ ] **Step 4: Run formatter**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff format src/orchestrator/contracts.py src/orchestrator/capability_summary.py src/services/capability_resolver.py src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py`
Expected: Files already formatted or minor reformats applied

- [ ] **Step 5: Final commit if any formatting changes**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add -u
git diff --cached --stat  # Only commit if there are changes
git commit -m "style(spec1a): ruff format pass"
```

---

## Verification Checklist

After all tasks are complete, verify these success criteria from the spec:

| # | Criterion | How to verify |
|---|-----------|--------------|
| 1 | `PlanOutput` validates correctly with all field combinations | `pytest tests/test_plan_output.py` — 25 tests |
| 2 | `generate_capability_summary()` produces compact XML from tool registry | `pytest tests/test_capability_summary.py` — 12 tests |
| 3 | `discover_capabilities` tool returns filtered capabilities | `pytest tests/test_discover_capabilities.py` — 10 tests |
| 4 | `CapabilityResolver` maps capabilities to tools with schemas | `pytest tests/test_capability_resolver.py::TestResolve tests/test_capability_resolver.py::TestResolveForStep` — 8 tests |
| 5 | `route_step()` correctly routes capabilities to agents | `pytest tests/test_capability_resolver.py::TestRouteStep` — 8 tests |
| 6 | All new code has tests, no existing code broken | Full test suite passes |
| 7 | Existing `PlannerOutput` and routing continue unchanged | `pytest tests/test_contracts.py tests/test_contracts_v2.py` still pass |
