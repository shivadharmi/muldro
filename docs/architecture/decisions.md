# Key Design Decisions

## 1. Hub-and-Spoke Multi-Agent Topology

**Decision:** Route all agent interactions through a central `JarvisOrchestrator` rather than allowing agents to call each other directly.

**Rationale:**
- **Isolation** - Each agent has a defined scope; bugs in one agent don't cascade to others
- **Debuggability** - Every interaction flows through one point with full tracing
- **Independent model selection** - Planner uses Opus for deep reasoning, Persona uses Haiku for cost efficiency
- **Easy to add/remove agents** - New agents plug in without modifying existing ones
- **Budget control** - Central point for token tracking and degradation

**Trade-off:** Slightly higher latency from routing through the orchestrator. Acceptable because agent calls dominate latency (Claude API), not routing logic.

## 2. Multi-Store Architecture with Postgres as Source of Truth

**Decision:** Use 5 infrastructure services (Postgres, Redis, Qdrant, Neo4j, MinIO/S3) with Postgres as the canonical source of truth and all others as projections or specialized stores.

**Rationale:**
- **Postgres as source of truth** - ACID guarantees, JSONB flexibility, native tsvector FTS with GIN indexes for keyword search
- **Qdrant for vector search** - Dedicated vector DB for high-volume RAG; supports multi-collection semantic search
- **Neo4j for graph traversal** - Multi-hop entity queries, shortest-path, community detection are graph-native operations that would be expensive recursive CTEs in Postgres
- **MinIO/S3 for objects** - Documents, screenshots, and media don't belong in Postgres; S3-compatible storage with presigned URLs
- **Redis for operational concerns** - Streams (event bus), caching, distributed locks, pubsub, surface tracking — all latency-sensitive ops that shouldn't hit Postgres

**Key principle:** Every secondary store can be rebuilt from Postgres. If Qdrant or Neo4j go down, the system degrades gracefully (Postgres FTS provides keyword search, entity tables provide flat queries) but continues operating.

**Previous decision (reversed):** Elasticsearch was originally included for BM25 full-text search but was removed in favor of Postgres native tsvector + GIN indexes. The operational complexity of a separate search cluster was not justified given Postgres's capable FTS implementation. pgvector embedding columns on Postgres tables were also removed (migration 046) in favor of Qdrant-only vector storage.

**Trade-off:** Operational complexity of 5 services. Mitigated by:
- All secondary services are optional (graceful degradation)
- Docker Compose provides all services for local dev
- `TriSearchService` coordinates Qdrant + Postgres FTS + Neo4j, `GraphSyncService` handles Neo4j sync
- Single configuration point via pydantic-settings

## 3. Approval Gates on All External Writes

**Decision:** Every external write (sending emails, posting messages, creating PRs) requires explicit user approval in v1.

**Rationale:**
- **Safety-first** for a system that acts on behalf of a founder
- **Trust building** - Users see every action before it happens, building confidence
- **Reversibility** - Prevents sending wrong emails, posting to wrong channels
- **Graduated autonomy** via trust scores that can relax the requirement over time

**Trade-off:** Slower execution for routine tasks. Mitigated by:
- `full_auto` policy mode for trusted action types
- Trust score tracking (approved_count, rejected_count)
- Plan-level approvals (approve once, execute all steps)

## 4. ULID with Type Prefixes

**Decision:** Use ULID (Universally Unique Lexicographically Sortable Identifier) with type prefixes (`evt_`, `mem_`, `plan_`, etc.) for all primary keys.

**Rationale:**
- **Time-sortable** - ULID encodes timestamp, enabling efficient range queries
- **Human-readable** - `apr_01HWQX46...` immediately tells you it's an approval
- **Collision-free** - 128-bit randomness, safe across distributed systems
- **No composite keys** - Simple foreign key relationships
- **Debuggable** - Copy-paste an ID and instantly know which table to query

**Trade-off:** Slightly larger than auto-incrementing integers. Negligible for this scale.

## 5. Bedrock Titan V2 for Embeddings

**Decision:** Use AWS Bedrock Titan V2 (1024 dimensions) for all vector embeddings, stored exclusively in Qdrant.

**Rationale:**
- **Cost-effective** - Significantly cheaper than OpenAI or Voyage for embedding generation
- **1024 dimensions** - Good balance between quality and storage/query performance
- **AWS ecosystem** - Consistent with Bedrock for Claude API and Reranker, simplifies auth/billing
- **Qdrant-only storage** - Dedicated vector DB outperforms pgvector; pgvector columns removed in migration 046

**Trade-off:** Slightly lower embedding quality than some specialized providers. Acceptable because embeddings are used for similarity search (dedup, retrieval ranking), not as primary classification.

## 6. 3-Tier Tool Dispatch

**Decision:** Resolve tools through three tiers: internal handlers -> MCP bridge -> ToolRegistry/connector fallback.

**Rationale:**
- **Graceful degradation** - If MCP servers are down, connectors still work
- **MCP-first** - Modern protocol with automatic tool discovery
- **Internal isolation** - Intelligence tools don't depend on MCP infrastructure
- **Extensibility** - New MCP servers auto-discovered, new connectors plug into Tier 3

**Trade-off:** Resolution logic is more complex. Mitigated by clear tier ordering and circuit breakers per MCP server.

## 7. Budget Degradation (Not Hard-Stop)

**Decision:** When budget is exhausted, degrade perception intervals (3x) and then disable perception entirely, rather than hard-stopping the entire system.

**Rationale:**
- **Ambient intelligence degrades gracefully** - User can still chat even if perception is paused
- **Critical notifications still work** - Approval requests, alerts continue
- **Predictable behavior** - Users understand "slower" better than "offline"
- **Daily reset** - Budget resets at midnight UTC, system auto-recovers

**Trade-off:** Budget can technically be exceeded by user-initiated chats even in paused mode. Acceptable because user-initiated work is always prioritized.

## 8. Contracts at Boundaries

**Decision:** Use Pydantic models (`PlannerOutput`, `AgentEnvelope`, `PolicyDecision`, `DomainEvent`) at all inter-service boundaries.

**Rationale:**
- **Schema drift detection** - Catch breaking changes at dev time, not production
- **Documentation** - Contracts self-document the interface
- **Graceful fallback** - `extra="ignore"` allows forward compatibility
- **Validation** - Literal types on decision fields catch typos and invalid states

**Trade-off:** Slight overhead from serialization/validation. Negligible compared to Claude API calls.

## 9. ServiceContainer with Optional Fields

**Decision:** The `ServiceContainer` dataclass has optional fields for all services, allowing partial initialization.

**Rationale:**
- **Graceful degradation** - If MemoryService fails to init, other services still work
- **Testing flexibility** - Inject only the services you need for a test
- **Startup resilience** - Individual service init failures don't block the system
- **Progressive enhancement** - New services can be added without breaking existing code

**Trade-off:** Null checks required at call sites. Mitigated by consistent `if service is not None` patterns.

## 10. Long-Lived DB Sessions for Services

**Decision:** Services use long-lived database sessions (created once at orchestrator init) rather than per-request sessions.

**Rationale:**
- **Avoids rapid session churn** - Agents make many DB calls per orchestrator cycle
- **Connection pool efficiency** - Fewer connection acquisitions
- **Transaction grouping** - Related operations share a session

**Trade-off:** Risk of stale connections. Mitigated by SQLAlchemy's connection pool with health checks and the startup recovery ensuring clean state.

## 11. Claude Structured Output with Text Fallback

**Decision:** The Planner uses Claude's `tool_use` for structured output (PlannerOutput schema) with a text-based JSON parser as fallback.

**Rationale:**
- **Deterministic schema** - tool_use enforces the exact PlannerOutput structure
- **Resilience** - If tool_use fails, text parsing recovers the decision
- **Debugging** - Text fallback is human-readable in traces
- **Cost savings** - tool_use has ~0% schema violation rate, reducing retry costs

**Trade-off:** Dual parsing logic. Worth it for the reliability improvement.

## 12. Fire-and-Forget Persona Learning

**Decision:** The Persona agent runs as a fire-and-forget task after every user interaction, not blocking the response.

**Rationale:**
- **No latency impact** - User gets their response immediately
- **Continuous learning** - Every interaction improves preference understanding
- **Cost-effective** - Uses Haiku (cheapest model)
- **No failure impact** - If Persona fails, the response was already delivered

**Trade-off:** Preferences may not be available for the immediately next interaction. Acceptable because preferences are long-term patterns, not per-message state.

## 13. Full Workspace Isolation

**Decision:** All 54 data tables are scoped by `workspace_id` (NOT NULL FK). Two resolution paths: API (session-based, zero queries) vs background (DB lookup via WorkspaceMember). Enables future multi-workspace support.

**Rationale:**
- **Security** - Data isolation is enforced at the schema level, not application logic
- **Multi-tenancy ready** - A single deployment can serve multiple workspaces
- **Zero-cost for API** - workspace_id comes from the session, no extra DB query
- **Background workers** - Resolve workspace via WorkspaceMember table when no session context

**Trade-off:** Every query must include workspace_id. Mitigated by consistent patterns and the fact that workspace_id is always available from auth context or DB lookup.

## 14. No Default Users

**Decision:** Every function requires explicit `user_id` from auth context. Background workers query user IDs from DB at startup. There are zero `"usr_default"` references in the codebase.

**Rationale:**
- **Security** - Eliminates shared-state risks from a magic default user
- **Auditability** - Every action is attributable to a real user
- **Multi-user ready** - System correctly handles multiple users from day one
- **Explicit over implicit** - Functions fail loudly if user_id is missing, rather than silently using a default

**Trade-off:** Slightly more complex startup (must query user IDs from DB) and wiring (user_id threaded through all call chains). Worth it for the security and correctness guarantees.

## 15. Runtime Contracts at Boundaries

**Decision:** Pydantic models (`PlannerOutput`, `PolicyDecision`, `StepResult`, `ToolCallRequest`) validate data at all agent and execution boundaries. Graceful fallback on validation failure.

**Rationale:**
- **Catch errors early** - Malformed agent output is caught before it propagates
- **Self-documenting** - Contract models define the exact interface between components
- **Resilient** - `extra="ignore"` allows forward compatibility; text fallback parsing if structured output fails
- **Type safety** - Literal types on decision/status fields prevent invalid states

**Trade-off:** Dual parsing logic (structured output + text fallback) for the Planner. Worth it because the fallback catches edge cases where Claude's tool_use doesn't fire.

## 16. Execution Model Consolidation

**Decision:** Removed the legacy `Execution` / `ExecutionTaskRun` models. Single execution path: `TaskRun` + `TaskStep` with state machine guards (`transition_run()` / `transition_step()`).

**Rationale:**
- **Single source of truth** - One execution model eliminates confusion about which table to query
- **State machine enforcement** - `InvalidTransitionError` prevents illegal status changes (e.g., completed -> running)
- **Simplified recovery** - Only TaskRuns need stale detection (running > 15min), no separate Execution recovery
- **Cleaner operator** - Removed legacy `_execute_sequential` fallback, always delegates to GraphExecutor

**Trade-off:** Migration required to move any in-flight Executions. Acceptable because the system was already using TaskRun as the primary path.

## 17. Real Cost Tracking

**Decision:** Track cache tokens (creation at 1.25x, read at 0.1x input price), thinking tokens (at output price), and per-agent cost. Fixed a critical bug where token usage was never committed to DB (flush without commit led to rollback on session close).

**Rationale:**
- **Accurate budgets** - Previous hardcoded `0.0` cost meant budget degradation never triggered
- **Cache awareness** - Prompt caching saves significant cost; tracking it shows real savings
- **Thinking visibility** - Opus thinking tokens are a major cost driver that was previously invisible
- **Per-agent attribution** - Know which agents consume the most budget (Planner/Opus vs Persona/Haiku)

**Trade-off:** Slightly more complex cost calculation. Mitigated by centralizing all cost logic in `BudgetTracker.calculate_cost()` with comprehensive tests (13 budget tests).
