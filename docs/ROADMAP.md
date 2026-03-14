# Jarvis Roadmap

## Milestone 1: Foundation (Complete)

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

## Milestone 2: Intelligence Layer (Complete)

- [x] Semantic memory search (pgvector embeddings with Voyage AI, cosine similarity)
- [x] Personalization: preference extraction (Claude-powered, category-scoped)
- [x] Proactive event-driven planning (auto-plan callback on event ingestion)
- [x] Importance scoring model tuning (context-aware: entities + preferences)
- [x] Heartbeat cron for priority re-evaluation (memory expiry + plan escalation)

## Milestone 3: User Experience (Complete)

### Sprint 5 — Canvas UI
- [x] Canvas dashboard endpoint (unified view: briefing + approvals + tasks + meetings)
- [x] Approval detail endpoint (execution context, plan goal, artifact refs)
- [x] Task detail endpoint (execution steps, progress, run results)
- [x] Plugin: jarvis_dashboard tool (Canvas-rendered dashboard)
- [x] Plugin: jarvis_approval_card tool (rich approval detail)
- [x] Plugin: jarvis_task_detail tool (task progress view)

### Sprint 6 — Slack + Notifications
- [x] Slack connector (Events API callback, message normalization, bot filtering)
- [x] Notification service (outbound Slack via webhooks)
- [x] Plugin: jarvis_notify tool

### Sprint 7 — Voice + WhatsApp
- [x] Voice service (TTS-friendly conversion)
- [x] WhatsApp connector stub (Business API webhook format)
- [x] Plugin: jarvis_voice tool

## Milestone 4: Hardening (Complete)

- [x] Retry and idempotency (exponential backoff decorator, SELECT FOR UPDATE on approvals)
- [x] Stale plan invalidation (TTL-based plan expiry, approval expiry enforcement in heartbeat)
- [x] Execution locks (PostgreSQL advisory locks, row-level locking on approvals)
- [x] Dead-letter queues (DLQ model + service, failed callback capture, retry tracking)
- [x] Observability dashboards (request tracing middleware, correlation IDs, metrics endpoint)
- [x] Security audit (rate limiting middleware, request size limits, CORS configuration)

## Milestone 5: Ecosystem Alignment (Complete)

Removed redundant plumbing that overlaps with OpenClaw's built-in capabilities.

### Cleanup
- [x] Remove source-specific connectors (Gmail, Calendar, Slack, WhatsApp, GitHub) — agent reads via gog/gh
- [x] Remove NotificationService — agent sends via message tool
- [x] Remove VoiceService — agent uses OpenClaw TTS
- [x] Remove source-specific webhook endpoints and schemas
- [x] Add generic `/v1/events/ingest` endpoint for agent-driven ingestion
- [x] Add `jarvis_ingest_event` and `jarvis_heartbeat` plugin tools

### OpenClaw Integration
- [x] OpenClawClient service (wake_agent, run_agent_turn, delegate_task)
- [x] Governor wakes agent on approval creation
- [x] Presenter wakes agent on briefing generation
- [x] Operator delegates external actions to OpenClaw agent (send_email, create_event, post_message)
- [x] Operator falls back to Claude for drafting/summarization when no agent available

### Redis Infrastructure
- [x] RedisCache (briefings, entity lookups, dedup window)
- [x] Redis-backed rate limiting (with in-memory fallback)
- [x] Redis distributed locks (alongside existing PG advisory locks)
- [x] Redis Streams task queue for async callback processing
- [x] CallbackWorker background processor

## Milestone 6: Production Deployment (Complete)

- [x] AWS infrastructure (Terraform: EC2, VPC, Route53, IAM, SSM Parameter Store)
- [x] Bedrock integration (backend `AsyncAnthropicBedrock` + OpenClaw `bedrock-converse-stream`)
- [x] Caddy reverse proxy with automatic TLS
- [x] Systemd services (jarvis-backend, openclaw, caddy)
- [x] User-data bootstrap script (9-phase automated provisioning)
- [x] Deploy script for code updates (`infra/scripts/deploy.sh`)
- [x] Daily Postgres backup with 7-day retention
- [x] Security hardening (UFW, fail2ban, SSH hardening, kernel sysctl)
- [x] Telegram channel integration
- [x] Production deployment on `jarvis.brrdcast.in`

## Next Up

- [ ] End-to-end acceptance tests (PRD scenarios)
- [ ] Policy modes (full_auto, suggest_only, approval_required, critical_only, lockdown)
- [ ] Real OAuth integration for data sources
- [ ] Monitoring SLOs (event latency < 2s, briefing < 5s, zero missed approvals, < 1% error rate)
- [ ] Multi-connector expansion (Notion, Drive)
- [ ] Dynamic API generation for task data
- [ ] Multi-agent routing (home/work personas)
- [ ] Mobile companion app
- [ ] Enterprise multi-user support
