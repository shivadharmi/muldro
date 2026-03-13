# Jarvis Roadmap

## Milestone 1: Foundation (Current)

**Goal**: Working daily briefing from Gmail + Calendar, with approval-gated email drafts.

### Sprint 1 — Infrastructure + Wiring
- [x] Project skeleton (backend + plugin)
- [x] Database models (SQLAlchemy)
- [x] API endpoint stubs
- [x] OpenClaw plugin with tools + routes
- [x] Docker Compose (Postgres + Redis)
- [ ] Git init + CI pipeline
- [ ] Alembic initial migration
- [ ] Wire planner service to command endpoint

### Sprint 2 — First Useful Flow
- [ ] Gmail connector (OAuth + push notifications)
- [ ] Event processor (normalize, score, dedupe)
- [ ] Entity extraction (people, projects from events)
- [ ] Planner v0 (Claude structured output → task graph)
- [ ] Basic daily briefing (text-based via chat)

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
