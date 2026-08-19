# Key Design Decisions

## 1. Hub-and-Spoke Multi-Agent Topology

**Decision:** Route all agent interactions through a central `MuldroOrchestrator` rather than allowing agents to call each other directly.

**Rationale:**
- **Isolation** - Each agent has a defined scope; bugs in one agent don't cascade to others
- **Debuggability** - Every interaction flows through one point with full tracing
- **Independent model selection** - Planner uses Opus for deep reasoning, Persona uses Haiku for cost efficiency
- **Easy to add/remove/merge agents** - Observer and Researcher were merged into Perceiver without modifying other agents
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

## 3. TrustEngine as Single Approval Gate

**Decision:** The `TrustEngine` in GraphExecutor is the single approval gate for all external writes, using a 4x4 matrix of trust_level (first_use, learning, trusted, autonomous) x risk_level (none, low, medium, high). Governor is not a routed agent — it is a deterministic policy service (`services/governor.py`) invoked as an audit-only pre-tool hook.

**Rationale:**
- **Safety-first** for a system that acts on behalf of a founder
- **Graduated autonomy** - Trust levels evolve based on interaction history, not static config
- **Fine-grained decisions** - 4 outcomes: `auto_execute_silent`, `auto_execute_notify`, `approval_required`, `blocked`
- **Reversibility** - High-risk actions always require approval regardless of trust level

**Trade-off:** Slower execution for new users. Mitigated by:
- Trust levels increase automatically as actions are approved
- `auto_execute_notify` allows execution while keeping users informed
- Plan-level approvals (approve once, execute all steps)

**Models:** `TrustState`, `TrustCeiling`, `InteractionLog`, `EngagementHistory` track trust evolution. `ApprovalPolicyEngine`, `TrustScore`, and `ApprovalPolicy` models were deleted.

> **Amended by [ADR 23](#23-two-independent-write-gates-and-the-prepare-verdict).** "Single gate" now means *one gate per question*, not one gate overall: `TrustEngine` remains the sole **per-capability** gate, and `permission_gate` is the sole **per-action** one. They are deliberately independent, and trust does not suppress permission.

## 4. ULID with Type Prefixes

**Decision:** Use ULID (Universally Unique Lexicographically Sortable Identifier) with type prefixes (`evt_`, `mem_`, `plan_`, etc.) for all primary keys.

**Rationale:**
- **Time-sortable** - ULID encodes timestamp, enabling efficient range queries
- **Human-readable** - `apr_01HWQX46...` immediately tells you it's an approval
- **Collision-free** - 128-bit randomness, safe across distributed systems
- **No composite keys** - Simple foreign key relationships
- **Debuggable** - Copy-paste an ID and instantly know which table to query

**Trade-off:** Slightly larger than auto-incrementing integers. Negligible for this scale.

## 5. Bedrock Titan V2 for Embeddings — SUPERSEDED by #21

> **Superseded (2026-07-20)** by [#21 Local embeddings + reranking (fastembed)](#21-local-embeddings--reranking-fastembed). Later reality also diverged from the "consistent with Bedrock for Claude API" rationale — Step 11 moved the LLM off Bedrock to the direct Claude API. Kept as history.

**Decision (original):** Use AWS Bedrock Titan V2 (1024 dimensions) for all vector embeddings, stored exclusively in Qdrant.

**Rationale:**
- **Cost-effective** - Significantly cheaper than OpenAI or Voyage for embedding generation
- **1024 dimensions** - Good balance between quality and storage/query performance
- **AWS ecosystem** - Consistent with Bedrock for Claude API and Reranker, simplifies auth/billing
- **Qdrant-only storage** - Dedicated vector DB outperforms pgvector; pgvector columns removed in migration 046

**Trade-off:** Slightly lower embedding quality than some specialized providers. Acceptable because embeddings are used for similarity search (dedup, retrieval ranking), not as primary classification.

**Interim (superseded within this project):** embeddings briefly moved to Voyage AI (via the MongoDB-hosted `ai.mongodb.com` endpoint) with Titan as fallback, after Bedrock Titan was SCP-blocked. That external dependency proved brittle (the endpoint retired `voyage-3`), leading to #12.

## 6. Unified Registry Dispatch

**Decision:** All tools served through MCP. One registry lookup dispatches to `internal_mcp`, `external_mcp`, or `composite` backend. Tool identity in 2 files (`catalog.py` + `intelligence_server.py`). Real MCP names used everywhere — no normalization.

**Rationale:**
- **Single source of truth** - Adding a tool: 1-2 files, not 8
- **No name normalization** - Real MCP names flow end-to-end (eliminates collision bugs like `search` vs Notion's `search`)
- **Auto-discovery** - Unknown MCP tools registered on connect with safe defaults (`capability=None` → invisible)
- **Startup validation** - 6 cross-checks catch inconsistencies before runtime
- **Capability-based auth** - Agents have capability scopes, not tool lists. Adding a tool with `email.send` capability automatically grants it to all agents with `email.send` in scope
- **Discoverability** - Planner can call `discover_capabilities` tool; `capability_summary` service provides a structured view of available capabilities

**Trade-off:** DB lookup per dispatch (mitigated by ToolRegistry cache). No offline fallback if DB is down (acceptable — Postgres is a hard dependency anyway).

## 7. Budget Degradation (Not Hard-Stop)

**Decision:** When budget is exhausted, degrade perception intervals (3x) and then disable perception entirely, rather than hard-stopping the entire system.

**Rationale:**
- **Ambient intelligence degrades gracefully** - User can still chat even if perception is paused
- **Critical notifications still work** - Approval requests, alerts continue
- **Predictable behavior** - Users understand "slower" better than "offline"
- **Daily reset** - Budget resets at midnight UTC, system auto-recovers

**Trade-off:** Budget can technically be exceeded by user-initiated chats even in paused mode. Acceptable because user-initiated work is always prioritized.

## 8. Contracts at Boundaries

**Decision:** Use Pydantic models (`PlanOutput`, `PlanStep`, `AgentEnvelope`, `PolicyDecision`, `DomainEvent`, `SurfaceUpdate`) at all inter-service boundaries.

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

## 10. Per-Request DB Sessions for Services (supersedes "long-lived sessions")

**Decision:** The API orchestrator holds only **session-free** singletons
(`build_shared`); every **DB-bound** service is built per request against a
fresh `AsyncSession` (`attach_session` / `request_services`). Background
single-flow callers (scheduler, OAuth-callback tasks) may still pass a full
container built with one session via `build()`.

**History:** This reverses the original "long-lived DB sessions" decision. A
single process-wide orchestrator (cached in `routes_chat`) shared one
`AsyncSession` across every service. An `AsyncSession` is **not safe for
concurrent use**, so two simultaneous chat requests touching a shared service
(e.g. `memory_service`, `world_model`) could collide ("another operation is in
progress") or interleave transactions. Several sites also mixed a fresh
request session with stale long-lived-session services in the same logical
operation.

**Rationale:**
- **Concurrency safety** - each request owns its session; none is shared.
- **Transaction consistency** - one operation uses exactly one session.
- **No churn for shared resources** - the Redis client, vector store, graph
  engine, reranker, and OAuth manager remain process-wide singletons reused by
  identity; only the cheap DB-bound service objects are rebuilt per request.

**Mechanism:** `build_shared(settings)` builds the singletons once;
`request_services(base, settings, db)` reuses an already-wired container (tests
/ single-flow) or calls `attach_session(base, settings, db)` for the shared
container. Each caller exposes a thin `_request_services(db)` bridge.

**Trade-off:** Per-request service construction, but these are lightweight
wrappers over the shared singletons — negligible next to a Claude API call.

## 11. Claude Structured Output with Text Fallback

**Decision:** The Planner uses Claude's `tool_use` for structured output (PlanOutput schema) with a text-based JSON parser (`extract_plan`) as fallback.

**Rationale:**
- **Deterministic schema** - tool_use enforces the exact PlanOutput structure with capability-based steps
- **Resilience** - If tool_use fails, text parsing via `extract_plan` recovers the plan
- **Debugging** - Text fallback is human-readable in traces
- **Cost savings** - tool_use has ~0% schema violation rate, reducing retry costs
- **Validation** - Circular dependency validator ensures step DAGs are acyclic

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

**Decision:** All data tables are scoped by `workspace_id` (NOT NULL FK). Two resolution paths: API (session-based, zero queries) vs background (DB lookup via WorkspaceMember). Enables future multi-workspace support.

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

**Decision:** Pydantic models (`PlanOutput`, `PlanStep`, `PolicyDecision`, `StepResult`, `ToolCallRequest`, `SurfaceUpdate`) validate data at all agent and execution boundaries. Graceful fallback on validation failure.

**Rationale:**
- **Catch errors early** - Malformed agent output is caught before it propagates
- **Self-documenting** - Contract models define the exact interface between components
- **Resilient** - `extra="ignore"` allows forward compatibility; text fallback parsing if structured output fails
- **Type safety** - Literal types on status fields prevent invalid states; circular dependency validation on PlanStep DAGs

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

**Trade-off:** Slightly more complex cost calculation. Mitigated by centralizing all cost logic in `BudgetTracker.calculate_cost()` with comprehensive tests.

## 18. Capability-Based Routing

**Decision:** Replace decision-type routing (`RouteResolver` with 19 decision types mapped to agent pipelines) with capability-based routing (`CapabilityResolver` mapping step capabilities to agents).

**Rationale:**
- **Composable** - Plans are sequences of capability-tagged steps, not monolithic decision types
- **Extensible** - Adding a new capability does not require a new decision type or route definition
- **Agent-agnostic** - Steps declare what capability they need, not which agent runs them
- **DAG-native** - Steps have `depends_on` fields forming a directed acyclic graph validated at plan creation
- **Discoverable** - Planner calls `discover_capabilities` to learn what the system can do

**Deleted:** `RouteResolver`, `route_analytics`, `agent_routes` table, `DEFAULT_ROUTES`, all 19 decision type constants. The `CapabilityResolver` (`src/services/capability_resolver.py`) and `capability_summary` service (`src/services/capability_summary.py`) replace them.

> **Extended by [ADR 22](#22-one-chat-shape-a-single-plan-scoped-lead).** The chat path took the "agent-agnostic" bullet to its conclusion and stopped selecting an agent at all: a capability now determines the turn's *authority* rather than its *routing*. Per-step capability→agent routing survives on the autonomous path.

**Trade-off:** Plans are slightly more complex (steps with capabilities vs a single decision string). Worth it because multi-step plans with mixed capabilities are now first-class.

## 19. Single TrustEngine Gate

**Decision:** Replace the triple approval gate (Governor pre-hook + ApprovalPolicyEngine + Governor service) with a single `TrustEngine` in GraphExecutor using a 4x4 trust_level x risk_level matrix.

**Rationale:**
- **Single gate** - One evaluation point instead of three, eliminating conflicting decisions
- **Graduated autonomy** - Four trust levels (first_use, learning, trusted, autonomous) evolve based on real interaction history
- **Four outcomes** - `auto_execute_silent`, `auto_execute_notify`, `approval_required`, `blocked` (not just approve/reject)
- **Risk assessment** - `RiskAssessor` (`src/services/risk_assessor.py`) evaluates step risk independently from trust

**Deleted:** `ApprovalPolicyEngine`, `TrustScore` model, `ApprovalPolicy` model. Governor is now `edge_case_only=True` (audit-only).

**New models:** `TrustState`, `TrustCeiling`, `InteractionLog`, `EngagementHistory`.

> **Amended by [ADR 23](#23-two-independent-write-gates-and-the-prepare-verdict).** The collapse from *three* conflicting gates to one still holds. What was added later is a second gate asking a genuinely different question — see ADR 23 for why that is not a regression to the pre-19 state.

**Trade-off:** Trust must be earned over time (new users face more approval prompts). Acceptable because safety is the priority, and trust levels increase automatically as actions are approved.

## 20. Signal-Driven Perception

**Decision:** Replace fixed-interval perception polling with signal-driven perception using a relevance assessor and tiered notification system.

**Rationale:**
- **Efficient** - Only process events that pass relevance scoring, reducing unnecessary API calls
- **Context-aware** - `RelevanceAssessor` (`src/services/relevance_assessor.py`) scores events against user context and active plans
- **Tiered delivery** - `EngagementService` (`src/services/engagement_service.py`) routes notifications based on urgency and user preferences
- **Memory-efficient** - `EvictionService` (`src/services/eviction_service.py`) manages perception state lifecycle

**New services:** `relevance_assessor.py`, `engagement_service.py`, `eviction_service.py`, `briefing_read_model.py`, `surface_detail_builders.py`.

**Trade-off:** More complex perception pipeline. Mitigated by clear service boundaries and the Perceiver agent (merged from Observer + Researcher) having a single `PERCEIVER_PROMPT` with 7-step read-only processing.

## 21. Local Embeddings + Reranking (fastembed)

**Decision (2026-07-20):** Generate embeddings and rerank search results **entirely on-host** via [fastembed](https://github.com/qdrant/fastembed) (ONNX runtime, no torch, no external API). Supersedes [#5](#5-bedrock-titan-v2-for-embeddings--superseded-by-21).
- **Embeddings:** `BAAI/bge-base-en-v1.5` (768-dim, MIT). `EmbeddingService` (`src/services/embedding_service.py`).
- **Reranking:** `Xenova/ms-marco-MiniLM-L-12-v2` cross-encoder (Apache-2.0). `RerankerService` (`src/services/reranker_service.py`).
- Both services load the model **lazily once** (thread-safe singleton) and run inference inside `asyncio.to_thread`. Model choices are configurable via `MULDRO_EMBEDDING_MODEL` / `MULDRO_RERANKER_MODEL`.

**Rationale:**
- **No external AI-API dependency** - removes AWS Bedrock (Titan embeddings, `amazon.rerank-v1:0`) and the MongoDB-hosted Voyage endpoint. No API keys, no per-call cost, no outage/deprecation surface (the immediate trigger: MongoDB's Voyage endpoint retired `voyage-3`, and Bedrock Titan was SCP-blocked).
- **Lightweight footprint** - fastembed pulls only `onnxruntime` + `tokenizers` (tens of MB), **not torch** (~2 GB). Fits the single-EC2 deploy; fastembed is the Qdrant ecosystem's library, already adjacent to our vector store.
- **CPU-adequate** - bge-base embeddings and the MiniLM-L-12 cross-encoder run in sub-second-to-few-seconds on CPU for typical batches; reranking is a nice-to-have re-scoring step with a graceful fallback to original ordering.

**Consequence:** the embedding vector dimension changed 1024 → **768**; `vector_store.VECTOR_SIZE = 768` and the Qdrant collections must be created at 768. Switching embedding models is a breaking change for stored vectors (different space + dim) — recreate the collections. `ensure_collections` logs a loud error if an existing collection's dim ≠ the model's.

**Trade-off:** Lower ceiling than the largest hosted rerankers/embedders and English-primary (bge-base / MiniLM). Acceptable for retrieval/dedup ranking; upgrade path is a heavier fastembed model (e.g. `bge-reranker-v2-m3`) if quality/multilingual coverage falls short.

**Deploy note:** model weights (~0.2 GB embed + ~0.12 GB rerank) download on first use to the fastembed cache, which defaults to an **OS temp dir** (e.g. `/tmp/fastembed_cache`) that can be purged — set a persistent `cache_dir` (`FASTEMBED_CACHE_PATH`) and pre-download at build/deploy so a temp-purge doesn't silently force a re-download and the first request doesn't stall.

## 22. One Chat Shape: a Single Plan-Scoped Lead

**Decision (2026-08-19):** The chat path runs **one lead per turn**, built with the union of the plan's step capabilities and left to discover its own tools. The per-step arm — which routed each `PlanStep` to an agent by identity and then ran a Presenter step to word the reply — is deleted, along with the `deep_single_lead` flag that had gated the alternative.

**Rationale:**
- **Agent identity was the last routing key left.** Everything else in the system had already moved to capability-based authority ([#18](#18-capability-based-routing)); the chat arm was the sole holdout, and keeping it meant two divergent chat implementations.
- **A plan is a statement of authority, not a script.** `derive_lead_scope` reads the plan for what the turn is *allowed* to do and hands the lead that scope; the lead decides sequencing. This is the same "agentic, not scripted" principle already applied to step execution.
- **Fail-closed by construction.** A read-only plan yields a lead with no write capability; a write plan grants that plan's writes and never the Executor's full ~50-capability write union. Against the shared executor singleton, the lead **tightens** the enforced floor rather than widening it.
- **One reply, one author.** With no Presenter hand-off, the reply cannot drift from what the lead actually did.

**What survives:** `CapabilityResolver.resolve_for_step` (autonomous path) and `capabilities_for_step` (feeds `derive_lead_scope`); `classify_capability_agent` for `runtime_projection`; `resolve_plan_routing` as a pure filter selecting the steps the *user* must perform; and deterministic per-step execution of `system.*` steps ahead of the lead.

**Scope of "one shape":** one shape *by default*. `settings.chat_planless` (default off) still reroutes a turn to a planless lead scoped from the workspace's standing connector scope rather than from a plan, skipping the Planner and the plan record entirely. That fork is out of this decision's scope and is tracked separately — it is named here so "one chat shape" is not read as "no flag-gated chat fork exists".

**Deleted:** `settings.deep_single_lead`, `can_pause`, `capability_resolver.route_step`, `presenter_skip`, and `chat_pipeline`'s four Presenter prompt builders.

**Trade-off:** One agent now holds a broader (though still plan-bounded) scope for the length of a turn, instead of each step holding a narrow one. Accepted because the union is derived from the same per-step authority the old arm used, so the *ceiling* is unchanged — and because the enforcement point (`capability_scope` middleware) is unchanged too. The behaviour-preservation evidence is `tests/test_chat_event_sequence.py`, whose expectations were captured from the legacy arm immediately before deleting it.

## 23. Two Independent Write Gates, and the PREPARE Verdict

**Decision (2026-08-19):** Keep **two** write gates that answer different questions, and give both a third verdict.

- `trust_gate` (TrustEngine) asks a per-**capability** question: *has the founder approved this capability often enough to stop being asked?* It is active only for gated `authorization_source`s — i.e. the autonomous path and any non-chat caller of `process_message`.
- `permission_gate` asks a per-**action** question: *is **this** write irreversible, externally visible, or high-risk?* It is active whenever the turn's effective `permission_mode` is `ask` or `auto`.

`trust_gate` is **outer**; its `auto_execute_*` verdict is a pass-through (`await handler(request)`), so the call still reaches `permission_gate`, which never reads trust.

**Rationale:**
- **Accrued capability trust must not authorise a novel action.** Without this composition, twenty-five approved self-scoped `email.send` calls would silently authorise a send to a brand-new external counterparty. Trust is evidence about a *capability*; it is not evidence about an *action*.
- **Two questions, two gates** is the honest decomposition. Collapsing them would mean either losing graduated autonomy or losing per-action risk sensitivity.

**The third verdict — PREPARE.** A gate that must stop a write has historically had two options, and on a turn with no human present both are wrong: interrupting either stalls the turn or orphans a checkpoint, and executing anyway is an ungated write. So `presence` (`present` | `absent`) is an explicit turn-level fact, and when it is `absent` a stop becomes **prepare**: record the action as an `Approval` (`approval_type="prepared_action"`) with the redacted payload and a snapshot of the acting agent's `capability_scope`, return a `status="success"` ToolMessage, and let the turn finish everything else. A turn with three writes reports *"I did these two and prepared this one."*

- **`presence` may only downgrade authority**, never grant it — `bypass` + absent → `auto`, because "do not interrupt me" is only meaningful when there is a *me*. It replaces `can_pause`, a transport boolean that had been silently acting as an authority input.
- **Confirmation replays the recorded payload** (`services/prepared_actions.py`), never re-running an agent — routing it back through `GraphExecutor` would re-*derive* the action instead of executing the reviewed one. It is checked against the scope snapshot, fails closed on every way the recorded action could fail to be the reviewed one (missing tool name, unknown tool, no capability, registry drift, out-of-scope capability, missing snapshot, truncated payload, unreadable payload), and is exactly-once via the idempotency ledger.
- **Delivery is calm** ([soul](../soul.md) laws 3 and 10): prepared work is not announced per item. Discovery is the `prepared_work` queue card, plus one pointer line the Presenter injects into the briefing's context (LLM-mediated, not a guaranteed literal). The queue is the only place an item can be acted on.

**Consequence, stated plainly:** a scheduled write that is irreversible, external/public, or high-risk is staged at **every** trust level including `autonomous`. Graduation-to-silent applies only to what `trust_gate` alone would have gated. If a scheduled task appears to have stopped working, check the prepared-work queue before debugging it.

**Trade-off:** `bypass` remains a real escape hatch but is fenced to a present, workspace-entitled user, and is transitional — build nothing new on it.
