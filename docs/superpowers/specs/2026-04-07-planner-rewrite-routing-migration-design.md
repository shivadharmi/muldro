# Spec 1B: Planner Rewrite & Routing Migration

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1A (Capability Infrastructure) — needs PlanOutput model, CapabilityResolver, capability summary, discover_capabilities tool
**Builds toward:** Spec 2 (Trust), Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

Spec 1A built the capability infrastructure (PlanOutput model, CapabilityResolver, capability summary generator, discover_capabilities tool). This spec **wires it in** — replacing the 19-decision-type routing with capability-based routing, rewriting the Planner prompt, merging Observer+Researcher into Perceiver, expanding the fast path, and deleting all old routing code.

This is the high-risk half of the Planner redesign. Every change here modifies existing behavior.

## Design

### Component 1: Rewritten Planner System Prompt

Replace `JARVIS_DECISION_FRAMEWORK` and the current `PLANNER_PROMPT` with a prompt that produces `PlanOutput`:

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
      "step_id": "step_1",
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
    {"step_id": "step_1", "description": "Get tomorrow's calendar events to find the meeting", "capability": "calendar.read", "input": {"time_range": "tomorrow"}, "risk": "none"},
    {"step_id": "step_2", "description": "Search recent emails with the investor", "capability": "email.search", "input": {"context": "investor meeting"}, "depends_on": ["step_1"], "risk": "none"},
    {"step_id": "step_3", "description": "Research investor's recent portfolio activity", "capability": "web.search", "input": {"query": "from step 1 and 2 context"}, "depends_on": ["step_1"], "risk": "none"},
    {"step_id": "step_4", "description": "Synthesize findings into talking points", "capability": "reason", "input": {"task": "create briefing from steps 1-3"}, "depends_on": ["step_1", "step_2", "step_3"], "risk": "none"},
    {"step_id": "step_5", "description": "Present briefing to user", "capability": "respond", "depends_on": ["step_4"], "risk": "none"}
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
    {"step_id": "step_1", "description": "Find yesterday's meeting details", "capability": "calendar.read", "input": {"time_range": "yesterday"}, "risk": "none"},
    {"step_id": "step_2", "description": "Search for related email thread", "capability": "email.search", "input": {"context": "investor follow-up"}, "risk": "none"},
    {"step_id": "step_3", "description": "Draft follow-up email based on meeting and prior thread", "capability": "email.draft", "input": {"context": "from steps 1-2"}, "depends_on": ["step_1", "step_2"], "risk": "medium"},
    {"step_id": "step_4", "description": "Send the drafted email", "capability": "email.send", "depends_on": ["step_3"], "risk": "high"}
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
    {"step_id": "step_1", "description": "Search for restaurants with availability tonight", "capability": "web.search", "input": {"query": "restaurants near me with availability tonight"}, "risk": "none"},
    {"step_id": "step_2", "description": "Present top options with booking links", "capability": "respond", "depends_on": ["step_1"], "risk": "none"},
    {"step_id": "step_3", "description": "User books the preferred restaurant", "actor": "user", "capability": "none", "depends_on": ["step_2"], "user_context": "Use the booking link or call the restaurant directly"}
  ],
  "capability_gaps": [{"description": "Restaurant booking API", "resolution": "Not currently available. Consider OpenTable or Resy MCP connector.", "workaround": "Web search for options + user books directly"}],
  "success_criteria": "User has restaurant options and booking information"
}
</examples>
```

### Component 2: Perceiver Agent (Observer + Researcher Merge)

Delete `OBSERVER_PROMPT` and `RESEARCHER_PROMPT`. Replace with single `PERCEIVER_PROMPT`:

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

<rules>
1. Never take write actions — only read and report
2. Read lists first (cheap), then details only for important items
3. If a tool call fails, report the error clearly
4. Summarize results with the most important items first
5. Include counts: "Found 12 unread emails, 3 are high priority"
6. For empty results, confirm explicitly: "No unread emails found"
7. Always cite sources with URLs when from the web
8. Don't make claims without evidence
9. Prioritize recent and high-confidence sources
</rules>
```

**Agent definition changes in `agents.py`:**
- Delete `observer` and `researcher` from `AGENTS` dict
- Add `perceiver` with:
  - `model_tier`: sonnet
  - `capability_scope`: Union of observer + researcher scopes (all read capabilities)
  - `thinking`: enabled (inherited from researcher config)
  - `max_tokens`: 4096
  - `temperature`: 0.3

### Component 3: Expanded Fast Path

Update `intent_classifier.py` to handle more intents without the full Planner:

**New fast intents added to `FAST_INTENTS`:**
- `direct_answer` — answerable from conversation context (no tools)
- `single_read` — clearly needs one read capability (e.g., "check my email")
- `memory_operation` — store/recall a fact
- `acknowledgment` — user confirming, no action needed

**New function `intent_to_plan()`** replaces `intent_to_decision()`:

```python
def intent_to_plan(intent: str, message: str, capabilities: list[str]) -> PlanOutput:
    """Generate a lightweight PlanOutput from a fast intent classification."""

    if intent in ("greeting", "chitchat", "acknowledgment"):
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description="Respond conversationally", capability="respond")],
            achievable="full",
            priority="low",
        )

    if intent == "direct_answer":
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description="Answer from context", capability="respond")],
            achievable="full",
            priority="medium",
        )

    if intent == "single_read":
        capability = _match_read_capability(message, capabilities)
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description=message, capability=capability, risk="none")],
            achievable="full",
            priority="medium",
        )

    if intent == "memory_operation":
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description=message, capability="knowledge.store")],
            achievable="full",
            priority="medium",
        )

    if intent in ("data_fetch", "status_query"):
        capability = _match_read_capability(message, capabilities)
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description=message, capability=capability, risk="none")],
            achievable="full",
            priority="medium",
        )

    if intent == "approval_response":
        return PlanOutput(
            goal=message,
            steps=[PlanStep(description="Process approval response", capability="respond")],
            achievable="full",
            priority="low",
        )

    # Fallback — shouldn't reach here for fast intents
    return PlanOutput(
        goal=message,
        steps=[PlanStep(description="Respond", capability="respond")],
        achievable="full",
    )


def _match_read_capability(message: str, capabilities: list[str]) -> str:
    """Match a user message to the most likely read capability.

    Uses keyword heuristics for common patterns. Falls back to 'knowledge.search'.
    """
    msg = message.lower()

    if any(w in msg for w in ("email", "mail", "inbox", "unread")):
        return "email.search"
    if any(w in msg for w in ("calendar", "schedule", "meeting", "event")):
        return "calendar.read"
    if any(w in msg for w in ("slack", "message", "channel")):
        return "messaging.read"
    if any(w in msg for w in ("github", "pr", "pull request", "issue", "repo")):
        return "repo.read"

    return "knowledge.search"
```

**Boundary rule:** `use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD`

### Component 4: Orchestrator Routing Rewrite

The core change — replace decision-type routing in `jarvis.py` with capability-based routing.

**Current flow (process_message / process_message_stream):**
```
classify_intent → [fast path: intent_to_decision → PlannerOutput] or [Planner call → extract_decision → PlannerOutput]
    → decision.decision == "set_goal" / "set_instruction" / etc → direct handlers
    → _resolve_pipeline(decision_dict) → agent pipeline from RouteResolver
    → for step in pipeline: _call_agent(step.agent, ...)
    → Presenter formats response
```

**New flow:**
```
classify_intent → [fast path: intent_to_plan → PlanOutput] or [Planner call → extract_plan → PlanOutput]
    → _persist_plan(plan) if plan.steps has write capabilities
    → _create_execution_surface(plan) for multi-step plans
    → for step in plan.steps:
        → route_step(step.capability) → agent name
        → if step.capability in ("reason", "respond"): direct LLM response
        → if step.actor == "user": present user action context, pause
        → CapabilityResolver.resolve_for_step(step) → focused tool list
        → _call_agent(agent, tools=focused_tools, message=step.description)
    → Presenter formats final response
```

**Key changes in `jarvis.py`:**

1. **Delete** `_resolve_pipeline()` method
2. **Delete** all `if decision.decision == "..."` conditional blocks (~20 locations)
3. **Delete** imports of `RouteResolver`, `DEFAULT_ROUTES`
4. **Replace** with a loop over `plan.steps` using `route_step()` and `CapabilityResolver`
5. **Update** `_persist_plan_record()` to accept `PlanOutput` instead of `PlannerOutput`
6. **Update** `_push_workspace_surface()` to work with `PlanOutput` (no `decision` field — use step capabilities for surface kind)
7. **Update** direct handlers: `set_goal`, `set_instruction`, `schedule_reminder`, `add_to_brief` become plan steps with special capabilities instead of decision-type checks. The Planner outputs these as `{capability: "system.set_goal"}` etc.
8. **Wire into BOTH** `process_message()` and `process_message_stream()`

**Surface kind mapping** (replaces decision-type mapping):
```python
def _capability_to_surface_kind(steps: list[PlanStep]) -> str:
    """Determine surface kind from plan step capabilities."""
    capabilities = {s.capability for s in steps if s.actor == "jarvis"}

    if any(c.endswith(".draft") or c.endswith(".send") for c in capabilities):
        return "recommendation"
    if any(c.startswith("web.") for c in capabilities) or "reason" in capabilities:
        return "summary"
    if "knowledge.store" in capabilities:
        return "summary"
    if len(steps) > 2:
        return "plan"

    return "summary"
```

### Component 5: GraphExecutor Integration

Update `GraphExecutor` to work with `PlanOutput` steps and use `CapabilityResolver`:

**Changes to `_populate_steps()`:** Accept `PlanOutput.steps` directly (already have `description`, `capability`, `depends_on`) instead of converting from `PlanTask`.

**Changes to `_run_step_action()`:** Use `CapabilityResolver.resolve_for_step(step.capability)` to get focused tool list for the Operator, instead of `_build_operator_tools()` which returns ALL operator tools.

**Changes to `_execute_step()`:** Read `step.capability` instead of `step.input_data.get("task_type")` for approval checks and routing.

### Component 6: Delete Old Routing

After all the above is working:

- **Delete** `src/services/route_resolver.py` entirely
- **Delete** `DEFAULT_ROUTES` constant
- **Delete** `JARVIS_DECISION_FRAMEWORK` from `prompts.py`
- **Delete** `OBSERVER_PROMPT` and `RESEARCHER_PROMPT` from `prompts.py`
- **Delete** `JARVIS_SOUL` legacy alias from `prompts.py`
- **Delete** `PlannerOutput`, `PlannerTask`, `InstructionSpec` from `contracts.py`
- **Delete** `intent_to_decision()` and `extract_decision()` from `intent_classifier.py`
- **Remove** `RouteResolver.seed_defaults()` from `app.py` startup
- **Drop or deprecate** `agent_routes` DB table (keep migration file, add new migration that drops table)

## Absorbed Issues from Audit

**Issue #22 — MCP tool name normalization is identity mapping:** With `CapabilityResolver` handling tool resolution, the identity mapping in the MCP bridge (`tool_mapping[t.name] = t.name`) is dead code. Delete it.

**Issue #10 — Telegram accesses orchestrator private attributes:** During the orchestrator refactor, add public methods:
- `orchestrator.get_budget_status() -> dict` — replaces `orchestrator._budget`
- `orchestrator.get_system_health() -> dict` — replaces `orchestrator._db_factory()` access
Update `telegram.py` `_handle_status()` to use these.

## Files Changed

### Deleted Files
- `src/services/route_resolver.py` — Entire file
- `src/services/route_analytics.py` — Depends on route_resolver

### Modified Files — Backend Core
- `src/orchestrator/jarvis.py` — Full routing rewrite in `process_message()` and `process_message_stream()`. Delete `_resolve_pipeline()`, delete 20+ decision-type conditionals, add plan step loop with `route_step()` + `CapabilityResolver`. Add public methods `get_budget_status()`, `get_system_health()`.
- `src/orchestrator/contracts.py` — Delete `PlannerOutput`, `PlannerTask`, `InstructionSpec`, `ExecutionPlan`. Keep `PlanOutput` (from Spec 1A).
- `src/orchestrator/prompts.py` — Delete `JARVIS_DECISION_FRAMEWORK`, `JARVIS_SOUL` alias, `OBSERVER_PROMPT`, `RESEARCHER_PROMPT`. Rewrite `PLANNER_PROMPT`. Add `PERCEIVER_PROMPT`.
- `src/orchestrator/agents.py` — Delete `observer`, `researcher`. Add `perceiver` (merged scopes). Update `AGENT_MODEL_TIERS`, `AGENT_CAPABILITY_SCOPES`, `AGENTS` dict.
- `src/orchestrator/intent_classifier.py` — Delete `intent_to_decision()`, `extract_decision()`. Add `intent_to_plan()`, `extract_plan()`, expanded `FAST_INTENTS`.
- `src/services/graph_executor.py` — Accept `PlanOutput` steps, use `CapabilityResolver.resolve_for_step()` for tool filtering, read `step.capability` instead of `task_type`.
- `src/orchestrator/tracing.py` — Update `SpanRecord.decision` from decision-type string to plan goal string.
- `src/interface/telegram.py` — Use `orchestrator.get_budget_status()` instead of `orchestrator._budget`. Use `orchestrator.get_system_health()` instead of `orchestrator._db_factory()`.

### Modified Files — Services
- `src/services/governor.py` — Update `evaluate_plan()` to work with `PlanOutput` (no `decision` field, iterate step capabilities for risk assessment).
- `src/services/metrics_service.py` — Update `PLANS_CREATED` counter label from `decision` to capability-based.
- `src/services/event_bus.py` — Update domain event payloads (no `decision` field).
- `src/services/surface_builder.py` — Update surface building from decision-type mapping to capability-based.
- `src/services/surface_detail_builders.py` — Update for new plan structure.
- `src/services/scheduler.py` — Update `_tick_background_tasks()` for `PlanOutput`, update "observer" references to "perceiver".
- `src/api/app.py` — Remove `RouteResolver.seed_defaults()` from startup.
- `src/api/routes_chat.py` — Update `MessageMetadata` to use `PlanOutput`. SSE stream emits `plan` event instead of `decision`.
- `src/api/routes_traces.py` — Update trace display for new structure.
- `src/ui/renderer.py` — Update `build_detail_config()` for capability-based plans.
- `src/models/agent_routes.py` — Deprecate model (add migration to drop `agent_routes` table).

### Modified Files — Frontend
- `frontend/src/lib/api.ts` — Replace `PlannerOutput` type with `PlanOutput`. Update `ChatSSEEvent` — `decision` event → `plan` event. Update `streamChat()` SSE parser.
- `frontend/src/lib/types.ts` — Delete types referencing `decision` field. Update `ConversationMessage.metadata_` to use `PlanOutput`.
- `frontend/src/lib/a2ui-types.ts` — Update `WorkspaceSurfacePush` — remove `decision` field.
- `frontend/src/lib/types/runtime.ts` — Update `RuntimeEventType` — remove decision-type events, add capability-based events.
- `frontend/src/lib/agent-config.ts` — Delete `observer`, `researcher`. Add `perceiver`. Demote `governor`.
- `frontend/src/components/jarvis/chat-panel.tsx` — Parse `plan` event instead of `decision`. Update agent step rendering for `perceiver`.
- `frontend/src/stores/activity-store.ts` — Update event type parsing.
- `frontend/src/components/shell/activity-strip.tsx` — Update event rendering.

### New Files
- Alembic migration to drop `agent_routes` table

## Testing Strategy

- Unit tests for new Planner prompt — sample inputs → verify PlanOutput JSON structure
- Unit tests for `intent_to_plan()` — all fast intents produce valid PlanOutput
- Unit tests for `extract_plan()` — parse raw Planner JSON response into PlanOutput
- Unit tests for `_match_read_capability()` — keyword matching for common patterns
- Unit tests for capability-based routing in orchestrator — plan steps routed to correct agents
- Unit tests for `_capability_to_surface_kind()` — capability sets → surface kinds
- Unit tests for GraphExecutor with PlanOutput steps — step creation, capability resolution, execution
- Integration tests: end-to-end message → plan → execution for:
  - Simple greeting (fast path, no Planner)
  - Single read ("check my email" → fast path → perceiver)
  - Multi-step action ("prepare for meeting" → Planner → perceiver + presenter)
  - Write action ("send email" → Planner → operator with approval)
  - Partial achievability ("book restaurant" → partial plan with user step)
  - Novel composition ("summarize GitHub PRs and email to team" → multi-capability plan)
- Regression: all current fast intents still work
- Frontend: SSE parser handles new `plan` event
- Frontend: agent steps render with `perceiver` agent name

## Success Criteria

1. The Planner produces multi-step plans with capability-level steps for complex requests
2. Simple requests skip the Planner — expanded fast path covers more intents
3. Adding a new MCP server automatically expands Planner's planning scope
4. The Operator receives only step-relevant tools via CapabilityResolver
5. Novel requests that compose existing capabilities produce reasonable plans
6. Partial/not_achievable plans are transparent about gaps
7. All 19 decision types eliminated — routing is capability-based
8. Observer and Researcher merged into Perceiver
9. Telegram uses public orchestrator methods
10. MCP identity mapping deleted
11. RouteResolver and DEFAULT_ROUTES deleted
12. `PlannerOutput` contract deleted — `PlanOutput` is the sole plan model

## Blast Radius

This is the highest-risk spec in the suite. It modifies the core routing logic that every message flows through.

### Tier 1: CRITICAL — Core routing (must all change together)

| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/jarvis.py` | Full routing rewrite — delete `_resolve_pipeline()`, delete ~20 decision-type conditionals, add plan step loop | Hub of the system |
| `src/orchestrator/contracts.py` | Delete `PlannerOutput`, `PlannerTask`, `InstructionSpec` | Core contracts consumed everywhere |
| `src/orchestrator/prompts.py` | Delete 3 prompts, rewrite 1, add 1 | Agent behavior definitions |
| `src/orchestrator/agents.py` | Delete 2 agents, add 1 | Agent registry |
| `src/orchestrator/intent_classifier.py` | Delete 2 functions, add 2, expand FAST_INTENTS | Fast path logic |

### Tier 2: HIGH — Execution and services

| File | What changes | Why |
|------|-------------|-----|
| `src/services/graph_executor.py` | PlanOutput steps, CapabilityResolver integration | Execution engine |
| `src/services/governor.py` | Work with PlanOutput (no `decision` field) | Approval evaluation |
| `src/api/routes_chat.py` | MessageMetadata uses PlanOutput, SSE `plan` event | Chat API |
| `src/services/route_resolver.py` | **DELETE** | Old routing |
| `src/api/app.py` | Remove seed_defaults() | Startup |

### Tier 3: MEDIUM — Dependent services and frontend

| File | What changes | Why |
|------|-------------|-----|
| `src/services/surface_builder.py` | Capability-based surface building | Surfaces |
| `src/services/scheduler.py` | PlanOutput format, "perceiver" references | Background tasks |
| `src/services/metrics_service.py` | Capability labels instead of decision labels | Metrics |
| `src/interface/telegram.py` | Public orchestrator methods | Telegram |
| `frontend/src/lib/api.ts` | PlanOutput type, `plan` SSE event | API client |
| `frontend/src/lib/types.ts` | Delete decision-based types | Domain types |
| `frontend/src/lib/agent-config.ts` | perceiver agent | Agent config |
| `frontend/src/components/jarvis/chat-panel.tsx` | Plan event parsing | Chat UI |

### Tier 4: Tests (will fail immediately — rewrite required)

| File | What changes | Why |
|------|-------------|-----|
| `tests/test_contracts.py` | Complete rewrite — references PlannerOutput | Contract tests |
| `tests/test_route_resolver.py` | **DELETE** (60+ tests) | Old routing tests |
| `tests/test_orchestrator.py` | Rewrite for new routing | Integration tests |
| `tests/test_planner_structured.py` | Rewrite for PlanOutput | Planner tests |
| `tests/test_perception_execution.py` | Update fixtures | Perception tests |
| `tests/test_ignore_decision.py` | Update for new handling | Decision tests |
| `tests/test_agent_registry.py` | Update for perceiver | Agent tests |
| `tests/golden/test_planner_decisions.py` | Complete rewrite | Golden tests |

### Tier 5: Documentation

| File | What changes |
|------|-------------|
| `CLAUDE.md` | Update Agent Routing, PlannerOutput refs, DEFAULT_ROUTES table, decision→pipeline mapping, Agent Boundaries table |
| `docs/architecture/message-flow.md` | New flow diagrams |
| `docs/architecture/decisions.md` | Replace 19 decision types |
| `docs/architecture/services.md` | Remove RouteResolver |

### Key Risk: String-Based Decision Type References

The 19 decision types appear as **string literals** across 30+ files. A grep sweep is required before deletion:

```bash
rg '"create_task"|"draft_reply"|"read_source"|"research"|"observe"|"remember"|"acknowledge"|"answer_directly"|"search_memory"|"add_to_brief"|"ignore"|"watcher_create"|"goal_update"|"recommend"|"summarize"|"schedule_reminder"|"set_goal"|"set_instruction"|"ask_user"' backend/src/
```

Every hit must be addressed. No backward compat — hard replacement.

### Total: ~45 files affected (20 backend source, 9 tests, 8 frontend, 2 deleted, 6 docs)
