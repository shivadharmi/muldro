# Jarvis Roadmap

## Milestone 1: Foundation (Current)

**Goal**: Working daily briefing from Gmail + Calendar, with approval-gated email drafts.

### Sprint 1 — Infrastructure + Wiring
- [x] Project skeleton (backend + plugin)
- [x] Database models (SQLAlchemy)
- [x] API endpoint stubs
- [x] OpenClaw plugin with tools + routes
- [x] Docker Compose (Postgres + Redis)
- [x] Git init + CI pipeline
- [x] Alembic initial migration
- [x] Wire planner service to command endpoint

### Sprint 2 — First Useful Flow
- [x] Gmail connector (test payload mode + push notification structure)
- [x] Event processor (normalize, score via Claude, dedupe by idempotency key)
- [x] Entity extraction (Claude-powered, upsert with alias resolution)
- [x] Planner v0 enrichment (event/entity/memory context injection)
- [x] Basic daily briefing (Presenter service, cached, Claude-generated)

### Sprint 3 — Memory + Approvals
- [x] Memory extraction service (Claude-powered, text-based retrieval, dedup)
- [x] Approval schema and flow (list/approve/reject with execution state)
- [x] Operator: draft email (Claude-powered, task graph execution)
- [x] Governor: policy rules v0 (action type + risk-based policy evaluation)
- [x] Audit logging (full correlation IDs, immutable trail)

### Sprint 4 — Calendar + Meeting Prep
- [x] Calendar connector (test payload mode + push notification structure)
- [x] Meeting entity and relationship support (via WorldModel extraction)
- [x] Meeting prep workflow (Presenter.generate_meeting_prep, Claude-powered)
- [x] Improved briefing with calendar data (upcoming meetings section)

## Milestone 2: Intelligence Layer

- [x] Semantic memory search (pgvector embeddings with Voyage AI, cosine similarity)
- [x] Personalization: preference extraction (Claude-powered, category-scoped)
- [x] Proactive event-driven planning (auto-plan callback on event ingestion)
- [x] Importance scoring model tuning (context-aware: entities + preferences)
- [x] Heartbeat cron for priority re-evaluation (memory expiry + plan escalation)

## Milestone 3: User Experience

### Sprint 5 — Canvas UI
- [x] Canvas dashboard endpoint (unified view: briefing + approvals + tasks + meetings)
- [x] Approval detail endpoint (execution context, plan goal, artifact refs)
- [x] Task detail endpoint (execution steps, progress, run results)
- [x] Plugin: jarvis_dashboard tool (Canvas-rendered dashboard)
- [x] Plugin: jarvis_approval_card tool (rich approval detail)
- [x] Plugin: jarvis_task_detail tool (task progress view)

### Sprint 6 — Slack + Notifications
- [x] Slack connector (Events API callback, message normalization, bot filtering)
- [x] Notification service (outbound Slack via webhooks, approval/briefing/execution alerts)
- [x] Slack webhook endpoint wired with full callback pipeline
- [x] Plugin: jarvis_notify tool (send notifications to channels)

### Sprint 7 — Voice + WhatsApp
- [x] Voice service (Claude-powered TTS-friendly conversion, markdown stripping)
- [x] WhatsApp connector stub (Business API webhook format, test payloads)
- [x] Voice endpoint (/v1/voice/convert)
- [x] Plugin: jarvis_voice tool (voice mode output)

## Milestone 4: Hardening

- [x] Retry and idempotency (exponential backoff decorator, SELECT FOR UPDATE on approvals)
- [x] Stale plan invalidation (TTL-based plan expiry, approval expiry enforcement in heartbeat)
- [x] Execution locks (PostgreSQL advisory locks, row-level locking on approvals)
- [x] Dead-letter queues (DLQ model + service, failed callback capture, retry tracking)
- [x] Observability dashboards (request tracing middleware, correlation IDs, metrics endpoint)
- [x] Security audit (rate limiting middleware, request size limits, CORS configuration)

## Future

- Multi-connector expansion (Notion, Drive, GitHub)
- Dynamic API generation for task data
- Multi-agent routing (home/work personas)
- Temporal workflow orchestration
- Mobile companion app
- Enterprise multi-user support
