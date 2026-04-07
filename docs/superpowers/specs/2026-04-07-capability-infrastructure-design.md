# Spec 1A: Capability Infrastructure

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 0 (Foundation Hardening)
**Builds toward:** Spec 1B (Planner Rewrite & Routing Migration)

## Problem Statement

The Jarvis Planner needs a capability abstraction layer before it can transition from classification-based routing to goal-decomposed planning. This spec builds the **infrastructure** that Spec 1B depends on — new models, new tools, and a resolution layer — without modifying any existing routing or contracts. Everything here is additive.

See the full problem statement in the parent spec context: the Planner classifies into 19 decision types instead of reasoning about capabilities, tool lists are hardcoded, and the Operator does the real planning.

## Design

### Core Principle

Build the three-level abstraction stack:
```
Level 1 — Goal (user speaks):     "Prepare for my investor meeting tomorrow"
Level 2 — Capability (Planner):   email.search → calendar.read → reason → respond
Level 3 — Tool (Operator):        search_gmail_messages → get_events → [LLM] → present
```

This spec creates everything at Level 2 (capability layer) and the Level 2→3 bridge (resolution). Spec 1B wires it into the actual Planner and routing.

### Component 1: PlanOutput Model

New contract models added to `contracts.py` **alongside** the existing `PlannerOutput` (not replacing yet — that's Spec 1B):

```python
class PlanStep(BaseModel):
    """A single step in a capability-based plan."""
    model_config = ConfigDict(extra="ignore")

    step_id: str = ""                          # Planner assigns sequential (step_1, step_2, ...)
    description: str                           # What this step does
    actor: Literal["jarvis", "user"] = "jarvis"  # Who performs this step
    capability: str                             # e.g., "email.search", "reason", "respond"
    input: dict[str, Any] = Field(default_factory=dict)  # Semantic input
    depends_on: list[str] = Field(default_factory=list)  # Step refs
    risk: Literal["none", "low", "medium", "high"] = "none"
    user_context: str | None = None            # For actor="user" — what they need to do


class PlanOutput(BaseModel):
    """Validated planner output — a goal-decomposed plan."""
    model_config = ConfigDict(extra="ignore")

    goal: str                                  # What the user wants
    reasoning: str = ""                        # Why this plan
    achievable: Literal["full", "partial", "not_achievable"] = "full"
    priority: Literal["low", "medium", "high", "critical"] = "medium"

    steps: list[PlanStep] = Field(default_factory=list)
    success_criteria: str = ""

    capability_gaps: list[CapabilityGap] = Field(default_factory=list)

    plan_id: str | None = None                 # Populated after persistence
    requires_user_input: bool = False          # True if any step has actor="user"


class CapabilityGap(BaseModel):
    """A capability the plan needs but doesn't have."""
    model_config = ConfigDict(extra="ignore")

    description: str
    resolution: str                            # "connect Notion" or "not currently possible"
    workaround: str | None = None              # Closest alternative
```

**Key:** These models are ADDED to contracts.py. `PlannerOutput` is untouched. Both coexist until Spec 1B does the switchover.

### Component 2: Capability Summary Generator

New file: `src/orchestrator/capability_summary.py`

Generates a compact capability summary (~200 tokens) from the tool registry and installation status, replacing 15-20K tokens of raw tool schemas in prompts.

```python
async def generate_capability_summary(db: AsyncSession, workspace_id: str) -> str:
    """Generate a compact capability summary for the Planner prompt.

    Queries tool_definitions grouped by capability family,
    cross-references with installations for connection status.
    Returns XML-formatted summary of connected/disconnected services.
    """
    from src.services.tool_registry import ToolRegistry

    registry = ToolRegistry(db)
    all_tools = await registry.list_tools(enabled_only=True)

    # Group by capability family (email, calendar, github, etc.)
    families: dict[str, list] = {}
    for tool in all_tools:
        if not tool.capability:
            continue
        family = tool.capability.split(".")[0]  # "email.search" → "email"
        if family not in families:
            families[family] = []
        action = tool.capability.split(".")[-1] if "." in tool.capability else tool.capability
        if action not in families[family]:
            families[family].append(action)

    # Check installation status for display names
    # ... query installations table for connected vs available-but-disconnected

    # Format as compact XML
    lines = ["<connected_services>"]
    for family, actions in sorted(families.items()):
        display = _family_display_name(family)  # "email" → "Email (Gmail)"
        lines.append(f"- {display}: {', '.join(sorted(actions))}")
    lines.append("</connected_services>")

    # Add disconnected services
    disconnected = await _get_disconnected_services(db, workspace_id, families)
    if disconnected:
        lines.append("\n<disconnected_services>")
        for name in disconnected:
            lines.append(f"- {name}: available but not connected")
        lines.append("</disconnected_services>")

    return "\n".join(lines)
```

### Component 3: Discover Capabilities Meta-Tool

New internal MCP tool that lets the Planner query available capabilities on demand.

**catalog.py addition:**
```python
InternalToolDef(
    name="discover_capabilities",
    description="Search available capabilities by query. Returns matching capabilities with descriptions, tools, risk levels, and connection status.",
    capability="system.discovery",
    risk_level="none",
    requires_approval=False,
)
```

**schemas.py addition:**
```python
class DiscoverCapabilitiesInput(BaseModel):
    """Search for available capabilities matching a query."""
    query: str  # e.g., "email", "calendar management", "code review"
```

**intelligence_server.py handler:**
```python
@mcp.tool()
async def discover_capabilities(query: str) -> dict:
    """Search available capabilities matching a query."""
    async with db_factory() as db:
        registry = ToolRegistry(db)
        all_tools = await registry.list_tools(enabled_only=True)

        # Filter tools whose capability or description matches query
        matches = []
        query_lower = query.lower()
        seen_capabilities = set()

        for tool in all_tools:
            if not tool.capability:
                continue
            if query_lower in (tool.capability or "").lower() or query_lower in (tool.description or "").lower():
                if tool.capability not in seen_capabilities:
                    seen_capabilities.add(tool.capability)
                    matches.append({
                        "capability": tool.capability,
                        "tools": [t.name for t in all_tools if t.capability == tool.capability],
                        "risk": tool.risk_level or "none",
                        "status": "connected",  # If tool is enabled, it's connected
                        "description": tool.description or "",
                    })

        return {"capabilities": matches}
```

### Component 4: Capability-to-Tool Resolution

New file: `src/services/capability_resolver.py`

Maps capability references in plans to specific tools at execution time. The Operator receives ONLY tools relevant to the current step.

```python
class CapabilityResolver:
    """Resolves capability references to specific tools.

    Given a capability like "email.search", finds the tool(s) that
    provide it and returns Claude API tool definitions.
    """

    def __init__(self, db: AsyncSession, workspace_id: str = ""):
        self._db = db
        self._workspace_id = workspace_id

    async def resolve(self, capability: str) -> list:
        """Find tools that provide a given capability."""
        from src.services.tool_registry import ToolRegistry

        registry = ToolRegistry(self._db)
        all_tools = await registry.list_tools(enabled_only=True)
        return [t for t in all_tools if t.capability == capability]

    async def resolve_for_step(self, step_capability: str) -> list[dict]:
        """Build Claude API tool definitions for a plan step.

        Returns tools for the step's capability PLUS related read tools
        (Operator may need to read context before writing).
        """
        primary = await self.resolve(step_capability)

        # Also include read tools from same family
        # e.g., for "email.send", also include "email.read" and "email.search"
        family = step_capability.split(".")[0] if "." in step_capability else ""
        related_read = []
        if family:
            from src.services.tool_registry import ToolRegistry
            registry = ToolRegistry(self._db)
            all_tools = await registry.list_tools(enabled_only=True)
            for t in all_tools:
                if (
                    t.capability
                    and t.capability.startswith(f"{family}.")
                    and not t.requires_approval
                    and t.capability != step_capability
                ):
                    related_read.append(t)

        # Convert to Claude API tool format
        tools = []
        from src.tools.schemas import TOOL_INPUT_MODELS

        for tool_def in primary + related_read:
            schema = TOOL_INPUT_MODELS.get(tool_def.name)
            if schema:
                tools.append({
                    "name": tool_def.name,
                    "description": schema.__doc__.strip() if schema.__doc__ else tool_def.description or tool_def.name,
                    "input_schema": schema.model_json_schema(),
                })
            else:
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
```

### Component 5: Capability Routing Function

A standalone routing function that maps capabilities to agents. This is built now but NOT wired into the orchestrator until Spec 1B.

```python
# In capability_resolver.py or new file src/orchestrator/capability_routing.py

async def route_step(step_capability: str, resolver: CapabilityResolver) -> str:
    """Route a plan step to the appropriate agent based on capability.

    Returns agent name: "presenter", "perceiver", "operator", "librarian"
    """
    # Pure reasoning / response — no tools needed
    if step_capability in ("reason", "respond", "none"):
        return "presenter"

    # Memory operations
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

## Files Changed

### New Files
- `src/orchestrator/capability_summary.py` — Capability summary generator
- `src/services/capability_resolver.py` — Capability-to-tool resolution + routing function

### Modified Files (Additive Only — no existing code broken)
- `src/orchestrator/contracts.py` — ADD `PlanOutput`, `PlanStep`, `CapabilityGap` (alongside existing `PlannerOutput`)
- `src/tools/catalog.py` — ADD `discover_capabilities` tool definition to `INTERNAL_TOOLS`
- `src/tools/schemas.py` — ADD `DiscoverCapabilitiesInput` model
- `src/tools/intelligence_server.py` — ADD `discover_capabilities` handler

### NOT Modified (saved for Spec 1B)
- `src/orchestrator/jarvis.py` — untouched
- `src/orchestrator/prompts.py` — untouched
- `src/orchestrator/agents.py` — untouched
- `src/orchestrator/intent_classifier.py` — untouched
- `src/services/route_resolver.py` — untouched
- `src/services/graph_executor.py` — untouched
- All frontend files — untouched

## Testing Strategy

- Unit tests for `PlanOutput` validation — valid plans, empty steps, partial achievability, capability gaps
- Unit tests for `PlanStep` — all field combinations, actor="user" steps, depends_on references
- Unit tests for `CapabilityGap` — description, resolution, workaround
- Unit tests for `generate_capability_summary()` — various connector states, empty workspace, mixed connected/disconnected
- Unit tests for `discover_capabilities` tool — query matching, no matches, partial matches
- Unit tests for `CapabilityResolver.resolve()` — single match, multiple matches, no match, unknown capability
- Unit tests for `CapabilityResolver.resolve_for_step()` — primary tools + related read tools
- Unit tests for `is_read_capability()` / `is_write_capability()` — read-only tools, write tools, mixed
- Unit tests for `route_step()` — reason→presenter, read→perceiver, write→operator, knowledge→librarian, unknown→operator
- Integration test: capability summary generates from real tool registry data
- Integration test: discover_capabilities tool returns correct results from registry

## Success Criteria

1. `PlanOutput` model validates correctly with all field combinations
2. `generate_capability_summary()` produces compact (~200 token) XML from real tool registry
3. `discover_capabilities` tool returns filtered capabilities from registry
4. `CapabilityResolver` maps capabilities to specific tools with schemas
5. `route_step()` correctly routes capabilities to agents
6. All new code has tests — no existing code is broken
7. Existing `PlannerOutput` and routing continue to work unchanged

## Blast Radius

**Minimal — this spec is entirely additive.**

### Files Modified

| File | What changes | Risk |
|------|-------------|------|
| `src/orchestrator/contracts.py` | ADD 3 new models (PlanOutput, PlanStep, CapabilityGap) | **LOW** — additive, no existing models changed |
| `src/tools/catalog.py` | ADD 1 tool definition | **LOW** — additive |
| `src/tools/schemas.py` | ADD 1 input model | **LOW** — additive |
| `src/tools/intelligence_server.py` | ADD 1 handler | **LOW** — additive |

### New Files (no blast radius)

| File | Purpose |
|------|---------|
| `src/orchestrator/capability_summary.py` | Capability summary generator |
| `src/services/capability_resolver.py` | Resolution + routing |
| `tests/test_capability_summary.py` | Summary generator tests |
| `tests/test_capability_resolver.py` | Resolver + routing tests |
| `tests/test_plan_output.py` | New model validation tests |

### Total: ~10 files (4 modified, 6 new) — no existing behavior changed
