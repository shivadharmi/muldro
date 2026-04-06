# Spec 1: Intelligent Planner & Capability-Based Routing

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** None (foundation spec)
**Builds toward:** Spec 2 (Trust), Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

The current Planner agent classifies user requests into 1 of 19 predefined decision types (`create_task`, `draft_reply`, `research`, etc.) and maps each to a fixed agent pipeline via the RouteResolver. This creates three structural problems:

1. **The Planner doesn't plan.** It classifies. The `PlannerOutput` is a label with a flat list of `PlannerTask` objects (`{task_type, input_data}`), not a reasoned multi-step strategy. The real planning happens in the Operator, which receives a vague semantic label and must figure out what tools to call and in what order.

2. **The system is heuristic, not autonomous.** 19 decision types is a closed set. Any request that doesn't map cleanly to one of these types falls to the `acknowledge` catch-all. Adding new capabilities requires adding new decision types, new routes, and new handlers — the system can't adapt to novel requests.

3. **Tool lists are hardcoded in agent prompts.** Agents receive static tool catalogs filtered by `capability_scope`. The Planner doesn't see tools at all. Adding a new MCP server doesn't automatically expand what the Planner can plan for.

### Soul/Vision Alignment Issues

- **Soul:** "Jarvis should feel like a calm strategist" — the Planner is a classifier, not a strategist
- **Vision Pillar 4:** "Reasoning and Planning — connect signals, identify patterns, frame options, reason under uncertainty, and plan actions or workflows" — hollow in current implementation
- **Soul:** "Only Planner decides intent" — violated because the Operator re-plans during execution

## Design

### Core Principle

The Planner reasons about **goals** using **capabilities**, not categories. The tool catalog defines what Jarvis CAN do. The Planner's job is to decompose ANY goal into steps that use available capabilities, without being constrained to predefined task types.

### Three Levels of Abstraction

```
Level 1 — Goal (user speaks):     "Prepare for my investor meeting tomorrow"
Level 2 — Capability (Planner):   email.search → calendar.read → reason → respond
Level 3 — Tool (Operator):        search_gmail_messages → get_events → [LLM] → present
```

The Planner operates at Level 2. The execution layer resolves Level 2 → Level 3. The user sees Level 1 and results.

### Component 1: Capability Summary Generator

Instead of injecting 120+ tool schemas into the Planner prompt (~15-20K tokens), generate a compact capability summary at request time from active connectors (~200 tokens).

**Input:** Active MCP installations + internal tool catalog
**Output:**

```xml
<connected_services>
- Email (Gmail): search, read, draft, send, label
- Calendar (Google): list events, create, update, delete
- GitHub: read issues/PRs, create issues, comment, create PRs
- Slack: read channels, post messages, add reactions
- Web Browser: navigate, search, read pages, fill forms
- Knowledge: search memories, store facts, search entities
- Notifications: send telegram, push to web
</connected_services>

<disconnected_services>
- Notion: available but not connected
- Linear: available but not connected
</disconnected_services>
```

**Implementation:** New function `generate_capability_summary(db, workspace_id) -> str` in a new file `src/orchestrator/capability_summary.py`. Queries `tool_definitions` table grouped by capability family, cross-references with `installations` table for connection status.

### Component 2: Discover Capabilities Meta-Tool

A single MCP tool available to the Planner that returns detailed capability information on demand.

**Tool definition:**
```python
# In catalog.py
InternalToolDef(
    name="discover_capabilities",
    description="Search available capabilities by query. Returns matching capabilities with descriptions and tool details.",
    capability="system.discovery",
    risk_level="none",
    requires_approval=False,
)
```

**Input schema:**
```python
class DiscoverCapabilitiesInput(BaseModel):
    """Search for available capabilities matching a query."""
    query: str  # e.g., "email", "calendar management", "code review"
```

**Output:** List of matching capabilities with their tools, risk levels, and connection status.

**Example:**
```
Input: {"query": "email"}
Output: {
  "capabilities": [
    {"capability": "email.search", "tools": ["search_gmail_messages"], "risk": "none", "status": "connected"},
    {"capability": "email.read", "tools": ["get_gmail_message_content"], "risk": "none", "status": "connected"},
    {"capability": "email.draft", "tools": ["draft_gmail_message"], "risk": "medium", "status": "connected"},
    {"capability": "email.send", "tools": ["send_gmail_message"], "risk": "high", "status": "connected"},
    {"capability": "email.label", "tools": ["modify_gmail_message_labels"], "risk": "low", "status": "connected"}
  ]
}
```

**Implementation:** New handler in `intelligence_server.py`. Queries `tool_definitions` table with capability-family text search.

### Component 3: New Planner Output Model

Replace `PlannerOutput` with a goal-decomposed plan:

```python
class PlanStep(BaseModel):
    """A single step in a plan."""
    model_config = ConfigDict(extra="ignore")

    step_id: str = ""                          # Planner assigns sequential (step_1, step_2, ...); orchestrator maps to ULID (step_01HX...)
    description: str                           # What this step does
    actor: Literal["jarvis", "user"] = "jarvis"  # Who performs this step
    capability: str                             # e.g., "email.search", "reason", "respond"
    input: dict[str, Any] = Field(default_factory=dict)  # Semantic input
    depends_on: list[str] = Field(default_factory=list)  # Step refs
    risk: Literal["none", "low", "medium", "high"] = "none"
    user_context: str | None = None            # For actor="user" steps — what they need to do

class PlanOutput(BaseModel):
    """Validated planner output — a goal-decomposed plan."""
    model_config = ConfigDict(extra="ignore")

    goal: str                                  # What the user wants
    reasoning: str = ""                        # Why this plan
    achievable: Literal["full", "partial", "not_achievable"] = "full"
    priority: Literal["low", "medium", "high", "critical"] = "medium"

    steps: list[PlanStep] = Field(default_factory=list)
    success_criteria: str = ""

    # Only when not fully achievable:
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)

    # Metadata
    plan_id: str | None = None                 # Populated after persistence
    requires_user_input: bool = False          # True if any step has actor="user"

class CapabilityGap(BaseModel):
    """A capability the plan needs but doesn't have."""
    model_config = ConfigDict(extra="ignore")

    description: str
    resolution: str                            # "connect Notion" or "not currently possible"
    workaround: str | None = None              # Closest alternative
```

**Key differences from current `PlannerOutput`:**
- No `decision` field (no classification into 19 types)
- Steps reference capabilities, not tool names
- Steps can have `actor: "user"` for handoff points
- `achievable` field for transparent capability gaps
- `success_criteria` for verification
- `capability` on each step enables routing: read capabilities → Perceiver, write capabilities → Operator, "reason"/"respond" → direct LLM

### Component 4: Expanded Fast Path

Not every request needs the full Planner. Expand the current intent classifier to handle more cases deterministically:

**Current fast intents (skip Planner):** greeting, chitchat, simple_question, data_fetch, status_query, approval_response

**Expanded fast intents:**
- **direct_answer:** Answerable from conversation context alone (no tools needed)
- **single_read:** Clearly needs one read tool (e.g., "check my email" → email.search)
- **memory_operation:** Store/recall a fact (e.g., "remember that John prefers Tuesday meetings")
- **acknowledgment:** User confirming something, no action needed

**Fast intent → lightweight plan generation:** Instead of calling the full Planner, `intent_to_plan()` generates a minimal `PlanOutput`:

```python
def intent_to_plan(intent: str, message: str, capabilities: list) -> PlanOutput:
    if intent == "single_read":
        # Detect which read capability is needed from the message
        capability = match_read_capability(message, capabilities)
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description=message, capability=capability, risk="none")],
            achievable="full",
        )
    elif intent == "direct_answer":
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description="Answer from context", capability="respond")],
            achievable="full",
        )
    # ... etc
```

**Boundary rule:** Can the task be accomplished with a single obvious capability? If yes, skip the Planner. If no (multi-step, ambiguous, novel), call the full Planner.

### Component 5: Capability-to-Tool Resolution

New resolution layer that maps capability references in plans to specific tools at execution time.

```python
# New file: src/services/capability_resolver.py

class CapabilityResolver:
    """Resolves capability references to specific tools."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def resolve(self, capability: str) -> list[ToolDefinition]:
        """Find tools that provide a given capability.

        Returns matching tools ordered by priority (preferred tool first).
        """
        # Query tool_definitions where capability matches
        # Return tools with their full schemas for the Operator
        ...

    async def resolve_for_step(self, step: PlanStep) -> list[dict]:
        """Build Claude API tool definitions for a specific plan step.

        Returns only the tools relevant to this step's capability,
        keeping the Operator's context focused.
        """
        tools = await self.resolve(step.capability)
        # Convert to Claude API tool format with schemas
        # Include related tools (e.g., for email.send, also include email.read
        # since Operator may need to read before writing)
        ...
```

**Key behavior:** The Operator receives ONLY the tools relevant to the current step, not the entire catalog. This keeps the Operator's context focused and prevents hallucinated tool calls.

### Component 6: Planner System Prompt

The Planner prompt is rewritten to produce goal-decomposed plans:

```
<role>
You are the Planner agent in Jarvis. You decompose user goals into
executable plans using available capabilities.
</role>

<available_capabilities>
{generated_capability_summary}
</available_capabilities>

<instructions>
For each user request:

1. Understand the goal — what does the user actually want?
2. Assess what's needed — external data? reasoning? action? just a response?
3. If you need more detail about available capabilities, call discover_capabilities
4. Decompose into steps using available capabilities
5. Identify dependencies between steps
6. Assess risk per step (none/low/medium/high)
7. If ambiguous, set requires_user_input: true and add an ask step

When capabilities are insufficient:
- Try to compose existing capabilities creatively
- For partial achievability, do what you can and mark user handoff steps
- For missing connectors, note them in capability_gaps with resolution
- Never pretend you can do something you can't

Output structured JSON only — the PlanOutput schema.
</instructions>

<output_format>
{
  "goal": "what the user wants",
  "reasoning": "why this plan",
  "achievable": "full | partial | not_achievable",
  "priority": "low | medium | high | critical",
  "steps": [
    {
      "description": "what this step does",
      "actor": "jarvis | user",
      "capability": "capability.name",
      "input": {},
      "depends_on": ["step_refs"],
      "risk": "none | low | medium | high"
    }
  ],
  "success_criteria": "how we know it worked",
  "capability_gaps": [],
  "requires_user_input": false
}
</output_format>

<examples>
User: "Prepare for my investor meeting tomorrow"
{
  "goal": "Prepare briefing for investor meeting",
  "reasoning": "Multi-step: need meeting details, investor context, and draft prep notes",
  "achievable": "full",
  "priority": "high",
  "steps": [
    {"description": "Get tomorrow's calendar events to find the meeting", "capability": "calendar.read", "input": {"time_range": "tomorrow"}, "risk": "none"},
    {"description": "Search recent emails with the investor", "capability": "email.search", "input": {"context": "investor meeting"}, "depends_on": ["step_1"], "risk": "none"},
    {"description": "Research investor's recent portfolio activity", "capability": "web.search", "input": {"query": "from step 1 and 2 context"}, "depends_on": ["step_1"], "risk": "none"},
    {"description": "Synthesize findings into talking points", "capability": "reason", "input": {"task": "create briefing from steps 1-3"}, "depends_on": ["step_1", "step_2", "step_3"], "risk": "none"},
    {"description": "Present briefing to user", "capability": "respond", "depends_on": ["step_4"], "risk": "none"}
  ],
  "success_criteria": "User has a briefing with meeting time, investor context, and 3+ talking points"
}

User: "Send a follow-up email to the investor from yesterday's meeting"
{
  "goal": "Draft and send investor follow-up email",
  "reasoning": "Need to read yesterday's meeting context, draft email, get approval to send",
  "achievable": "full",
  "priority": "high",
  "steps": [
    {"description": "Find yesterday's meeting details", "capability": "calendar.read", "input": {"time_range": "yesterday"}, "risk": "none"},
    {"description": "Search for related email thread", "capability": "email.search", "input": {"context": "investor follow-up"}, "risk": "none"},
    {"description": "Draft follow-up email based on meeting and prior thread", "capability": "email.draft", "input": {"context": "from steps 1-2"}, "depends_on": ["step_1", "step_2"], "risk": "medium"},
    {"description": "Send the drafted email", "capability": "email.send", "depends_on": ["step_3"], "risk": "high"}
  ],
  "success_criteria": "Follow-up email sent to investor with meeting context"
}

User: "Book me a restaurant for tonight"
{
  "goal": "Book a restaurant for tonight",
  "reasoning": "No restaurant booking capability available, but can search and assist",
  "achievable": "partial",
  "priority": "medium",
  "steps": [
    {"description": "Search for restaurants with availability tonight", "capability": "web.search", "input": {"query": "restaurants near me with availability tonight"}, "risk": "none"},
    {"description": "Present top options with booking links", "capability": "respond", "depends_on": ["step_1"], "risk": "none"},
    {"description": "User books the preferred restaurant", "actor": "user", "capability": "none", "depends_on": ["step_2"], "user_context": "Use the booking link or call the restaurant directly"}
  ],
  "capability_gaps": [{"description": "Restaurant booking API", "resolution": "Not currently available. Consider OpenTable or Resy MCP connector.", "workaround": "Web search for options + user books directly"}],
  "success_criteria": "User has restaurant options and booking information"
}
</examples>
```

### Component 7: Routing Changes

**Delete:** The 19-decision-type routing via `RouteResolver` and `DEFAULT_ROUTES` is no longer the primary routing mechanism.

**Replace with capability-based routing:**

```python
async def route_step(step: PlanStep, workspace_id: str) -> str:
    """Route a plan step to the appropriate agent based on capability."""

    capability = step.capability

    # Pure reasoning / response steps — no agent needed, direct LLM
    if capability in ("reason", "respond", "none"):
        return "presenter"

    # Read capabilities — Perceiver agent
    if is_read_capability(capability):
        return "perceiver"

    # Write capabilities — Operator agent
    if is_write_capability(capability):
        return "operator"

    # Memory operations — Librarian
    if capability.startswith("knowledge."):
        return "librarian"

    # Unknown — Operator as fallback (it has tool discovery)
    return "operator"
```

**`is_read_capability` / `is_write_capability`:** Derived from the tool registry's `requires_approval` and `risk_level` fields, not hardcoded lists. A capability is "write" if ANY of its tools have `requires_approval=True`.

### Agent Consolidation: Observer + Researcher → Perceiver

Both Observer and Researcher are read-only agents that gather information from external sources or internal knowledge. Merge into a single **Perceiver** agent.

**Perceiver prompt:**
```
<role>
You are the Perceiver agent in Jarvis — you gather information from any source.
You read external services (email, calendar, Slack, GitHub, web) and internal
knowledge (memories, entities, events). You are read-only: you never write,
create, or modify anything.
</role>

<workflow>
1. Understand what information is needed
2. Search internal knowledge first (memories, entities — cheaper)
3. If insufficient, fetch from external services
4. For web research, search first, then open URLs for deep reading
5. Cross-reference and validate across sources
6. Return structured findings with sources and confidence
</workflow>
```

**Capability scope:** Union of current Observer + Researcher scopes. All read capabilities.

### Migration Path

The 19 decision types and RouteResolver are not deleted immediately. Instead:

1. **Phase 1:** Add `PlanOutput` model alongside `PlannerOutput`. New Planner prompt produces `PlanOutput`. Old fast-path still produces `PlannerOutput`.
2. **Phase 2:** Add capability summary generator and `discover_capabilities` tool. Planner starts using them.
3. **Phase 3:** Add capability-based routing. Steps route by capability, not decision type.
4. **Phase 4:** Expand fast path to cover more intents. Fewer requests need full Planner.
5. **Phase 5:** Merge Observer + Researcher → Perceiver. Update capability scopes.
6. **Phase 6:** Delete old `PlannerOutput`, `DEFAULT_ROUTES`, decision-type routing. Full cutover.

Each phase is independently deployable and testable. Rollback = revert to previous phase.

## Files Changed

### New Files
- `src/orchestrator/capability_summary.py` — Capability summary generator
- `src/services/capability_resolver.py` — Capability-to-tool resolution layer

### Modified Files
- `src/orchestrator/contracts.py` — Add `PlanOutput`, `PlanStep`, `CapabilityGap` models
- `src/orchestrator/prompts.py` — New Planner prompt, new Perceiver prompt (merge Observer + Researcher)
- `src/orchestrator/agents.py` — Add Perceiver agent, update capability scopes
- `src/orchestrator/jarvis.py` — Capability-based routing in `process_message` and `process_message_stream`
- `src/orchestrator/intent_classifier.py` — Expanded fast intents, `intent_to_plan()` function
- `src/services/graph_executor.py` — Accept `PlanOutput` steps, resolve capabilities to tools per step
- `src/tools/catalog.py` — Add `discover_capabilities` tool definition
- `src/tools/schemas.py` — Add `DiscoverCapabilitiesInput` model
- `src/tools/intelligence_server.py` — Add `discover_capabilities` handler

### Deleted (Phase 6)
- `src/services/route_resolver.py` — Decision-type routing replaced by capability routing
- `DEFAULT_ROUTES` in route_resolver.py — No longer needed
- `agent_routes` DB table — No longer needed (capability routing is code, not DB)
- `JARVIS_DECISION_FRAMEWORK` in prompts.py — Replaced by new Planner prompt
- `OBSERVER_PROMPT` and `RESEARCHER_PROMPT` — Replaced by `PERCEIVER_PROMPT`

## Testing Strategy

- Unit tests for `PlanOutput` validation (valid/invalid plans, edge cases)
- Unit tests for capability summary generation (various connector states)
- Unit tests for `discover_capabilities` tool (query matching, filtering)
- Unit tests for capability-to-tool resolution (single match, multiple matches, no match)
- Unit tests for capability-based routing (read → perceiver, write → operator, reason → presenter)
- Integration tests: end-to-end message → plan → execution for each category (simple, multi-step, partial, novel)
- Regression tests: all current fast-intent cases still work
- Planner quality tests: sample prompts → verify plan decomposition quality

## Success Criteria

1. The Planner produces multi-step plans with capability-level steps for complex requests
2. Simple requests (greetings, single reads, direct answers) skip the Planner entirely
3. Adding a new MCP server automatically expands what the Planner can plan for (no code changes)
4. The Operator receives only step-relevant tools, not the full catalog
5. Novel requests that combine existing capabilities produce reasonable plans
6. Requests that exceed capabilities produce honest `partial`/`not_achievable` assessments
