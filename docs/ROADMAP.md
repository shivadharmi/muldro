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
- [ ] Memory extraction service
- [ ] Approval schema and flow
- [ ] Operator: draft email
- [ ] Governor: policy rules v0
- [ ] Audit logging

### Sprint 4 — Calendar + Meeting Prep
- [ ] Calendar connector (OAuth + polling)
- [ ] Meeting entity and relationship support
- [ ] Meeting prep workflow
- [ ] Improved briefing with calendar data

## Milestone 2: Intelligence Layer

- [ ] Semantic memory search (pgvector)
- [ ] Personalization: preference extraction
- [ ] Proactive event-driven planning
- [ ] Importance scoring model tuning
- [ ] Heartbeat cron for priority re-evaluation

## Milestone 3: User Experience

- [ ] Canvas UI: approval cards
- [ ] Canvas UI: briefing dashboard
- [ ] Canvas UI: task/progress view
- [ ] Voice integration (Talk Mode)
- [ ] WhatsApp/Slack channel integration

## Milestone 4: Hardening

- [ ] Retry and idempotency
- [ ] Stale plan invalidation
- [ ] Execution locks
- [ ] Dead-letter queues
- [ ] Observability dashboards
- [ ] Security audit

## Future

- Multi-connector expansion (Notion, Drive, GitHub)
- Dynamic API generation for task data
- Multi-agent routing (home/work personas)
- Temporal workflow orchestration
- Mobile companion app
- Enterprise multi-user support
