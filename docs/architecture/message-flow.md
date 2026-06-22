# Message Processing Pipeline

## User Chat to Response

When a user sends a message (via the web frontend), it flows through the following pipeline. Both `process_message()` and `process_message_stream()` require explicit `user_id` and `workspace_id` parameters (no default user):

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI /v1/jarvis/chat
    participant O as Orchestrator
    participant P as Planner (Opus)
    participant CR as CapabilityResolver
    participant A as Agent Pipeline
    participant TE as TrustEngine
    participant GE as GraphExecutor
    participant PR as Presenter
    participant PA as Persona (Haiku)

    U->>API: POST message + surface
    API->>API: Create/resume Conversation + Message (workspace_id scoped)
    API->>O: process_message_stream(user_id, workspace_id)
    O->>O: start_trace(trigger=user_message)

    Note over O,P: Step 1: Intent Classification
    O->>P: "Classify this message"
    P->>P: Structured output (PlanOutput)
    P-->>O: {goal, reasoning, achievable, priority, steps[], capability_gaps[]}

    Note over O,CR: Step 2: Capability Resolution
    O->>CR: resolve(step.capability)
    CR-->>O: agent assignment per step

    Note over O,A: Step 3: Pipeline Execution
    loop For each step in plan
        O->>A: _call_agent(agent, message)
        A->>A: Tool loop (max 10 rounds)
        A-->>O: agent_result
    end

    alt plan has executable steps
        Note over O,GE: Step 4: Plan Execution
        O->>TE: evaluate trust_level x risk_level (4x4 matrix)
        TE-->>O: PolicyDecision (auto_execute_silent/auto_execute_notify/approval_required/blocked)
        O->>GE: create_run() + execute_run()
        GE-->>O: run result
    end

    Note over O,PR: Step 5: Format Response
    O->>PR: "Present this result to the user"
    PR-->>O: formatted response

    Note over O,PA: Step 6: Learn Preferences (fire-and-forget)
    O->>PA: "Extract preferences from this interaction"

    O->>O: finish_trace()
    O-->>API: final response
    API-->>U: SSE event stream
```

## SSE Event Stream

The chat endpoint returns a Server-Sent Events stream. Each event has a `type` field:

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server

    S->>C: event: conversation {conversation_id}
    S->>C: event: trace {trace_id}

    rect rgb(240, 248, 255)
        Note over C,S: Per-Agent Block (repeats for each agent in pipeline)
        S->>C: event: agent_start {agent, model}
        S->>C: event: thinking {agent, text, is_thinking: true}
        S->>C: event: text_delta {agent, text}
        loop Tool Loop
            S->>C: event: tool_call {agent, tool, input}
            S->>C: event: tool_result {agent, tool, result}
        end
        S->>C: event: agent_done {agent, text, cost_usd, cache_creation_input_tokens, cache_read_input_tokens, thinking_tokens, latency_ms}
    end

    S->>C: event: plan {goal, reasoning, steps[], capability_gaps[]}

    opt If plan execution triggered
        S->>C: event: execution_start {run_id}
        S->>C: event: execution_result {status, steps_completed}
    end

    S->>C: event: response {text}
    S->>C: event: done {trace_id}
```

### Event Types Reference

| Event | Payload | When |
|-------|---------|------|
| `conversation` | `{conversation_id}` | Start of stream |
| `trace` | `{trace_id}` | Trace created |
| `agent_start` | `{agent, model}` | Agent begins work |
| `thinking` | `{agent, text, is_thinking: true}` | Agent reasoning (thinking blocks, Opus only) |
| `text_delta` | `{agent, text}` | Incremental text output |
| `tool_call` | `{agent, tool, input}` | Tool invocation |
| `tool_result` | `{agent, tool, result, blocked?, latency_ms?}` | Tool output |
| `agent_done` | `{agent, text, cost_usd, cache_creation_input_tokens, cache_read_input_tokens, thinking_tokens, latency_ms}` | Agent complete |
| `plan` | `{goal, reasoning, steps[], capability_gaps[]}` | Planner plan extracted |
| `execution_start` | `{run_id}` | GraphExecutor begins |
| `execution_result` | `{status, steps}` | Execution outcome |
| `response` | `{text}` | Final user-facing response |
| `error` | `{message}` | Error occurred |
| `done` | `{trace_id}` | Stream end |

## Planner Output

The Planner returns a structured `PlanOutput` (Pydantic model) with capability-based steps rather than decision types:

### PlanOutput Contract

```python
class PlanOutput(BaseModel):
    goal: str
    reasoning: str
    achievable: bool
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    steps: list[PlanStep] = []
    success_criteria: str = ""
    capability_gaps: list[CapabilityGap] = []
    plan_id: str | None = None
    requires_user_input: bool = False

class PlanStep(BaseModel):
    step_id: str
    description: str
    actor: str                    # agent name
    capability: str               # e.g., "email.read", "search.web"
    input: dict = {}
    depends_on: list[str] = []    # step_id references
    risk: str = "low"
    user_context: str = ""
```

The Planner uses Claude's `tool_use` structured output with a text fallback parser (`extract_plan`) for resilience. A circular dependency validator ensures step DAGs are acyclic.

## Capability Resolution

The `CapabilityResolver` (`src/services/capability_resolver.py`) maps step capabilities to agents. This replaces the former `RouteResolver` and decision-type routing.

Each `PlanStep` has a `capability` field (e.g., `email.read`, `search.web`, `memory.store`). The `CapabilityResolver` looks up which agent owns that capability via the agent's `capability_scope` and assigns the step accordingly. The Planner can discover available capabilities via the `discover_capabilities` tool and `capability_summary` service.

There are no hardcoded decision-to-agent mappings. Routing is purely capability-driven.

## Context Assembly

Four agents receive pre-loaded context (Planner, Presenter, Perceiver, Librarian):

```mermaid
graph TD
    CB[ContextBuilder] --> M[MemoryService<br/>Episodic + Preference memories]
    CB --> WM[WorldModel<br/>Relevant entities with importance]
    CB --> GM["MemoryService<br/>Goal memories (memory_type=goal)"]
    CB --> P[ProcedureLibrary<br/>Task-type procedures]

    M --> CP[ContextPack]
    WM --> CP
    GM --> CP
    P --> CP

    CB --> GR[GraphEngine<br/>Neo4j graph relationships]
    CB --> VS[VectorStore<br/>Qdrant semantic matches]
    CB --> PP[Preferences<br/>Explicit preference injection]

    M --> CP[ContextPack]
    WM --> CP
    GM --> CP
    P --> CP
    GR --> CP
    VS --> CP
    PP --> CP

    CP --> SP[System Prompt<br/>--- CONTEXT ---<br/>goals, entities, memories, procedures,<br/>graph relationships, preferences]
```

The `ContextPack` is converted to a markdown block appended to the agent's system prompt via `to_prompt(max_tokens)`. When the context exceeds `max_tokens`, it is truncated by priority order: goals > entities > events > preferences > artifacts > procedures. Memory retrieval uses a composite ranking formula (see [Services Reference](services.md)).

The `ContextBuilder` also accepts `graph_engine` (Neo4j) and `vector_store` (Qdrant) for enrichment. Explicit preferences are always injected via `get_user_preferences()` to ensure they influence decisions even when they do not match the current query semantically.

**Prompt architecture:** System prompts are split into `JARVIS_SOUL_CORE` (shared by all 7 agents) and `PLANNER_PROMPT_V2` (Planner-only 7-step decomposition). Perceiver has `PERCEIVER_PROMPT` (7-step read-only). This prevents non-Planner agents from making routing decisions.

## Streaming Implementation

The `_call_agent_stream()` method uses `client.messages.stream()` with thinking support for Opus. The stream emits:

- **thinking** events with `is_thinking: true` for Opus reasoning blocks
- **text_delta** events for incremental text output
- **tool_call** / **tool_result** events for tool invocations
- **agent_done** with full cost breakdown: `cost_usd`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `thinking_tokens`

All chat interactions create `Conversation` and `Message` records scoped by `workspace_id`.

## Runtime Contracts

All inter-agent communication is validated through Pydantic contracts:

- **PlanOutput** is validated after Planner returns (graceful fallback to text parsing via `extract_plan` on validation failure)
- **PlanStep** defines each step with capability, actor, dependencies, and risk
- **AgentEnvelope** / **AgentResult** wrap every `_call_agent()` invocation
- **PolicyDecision** returned by TrustEngine for plan evaluation (includes `auto_execute_notify` and `auto_execute_silent` modes)
- **StepResult** / **ToolCallRequest** / **ToolCallResult** used in GraphExecutor execution
- **SurfaceUpdate** tracks execution phases (plan_ready, executing, approval_needed, completed, failed) with emission points in GraphExecutor
