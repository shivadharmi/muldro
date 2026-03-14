# Jarvis Roadmap

## Design Principle: Don't Build What OpenClaw Already Does

OpenClaw provides: channels, agent runtime, sessions, workspace memory, multi-agent routing,
cron scheduling, OAuth/auth (gog/gh), voice, notifications, plugin ecosystem, mobile access.

Jarvis builds **only the intelligence layer** that OpenClaw cannot provide:
event scoring, world model, semantic memory, structured planning, governance,
execution orchestration, briefings, and audit.

---

## Phase 0: Foundation (Complete)

### Milestone 1: Infrastructure + First Flows
- [x] Project skeleton (backend + plugin)
- [x] Database models, Alembic migrations, Docker Compose
- [x] Event processor (normalize, score, dedupe)
- [x] Entity extraction (Claude-powered, alias resolution)
- [x] Planner v0 (context-enriched structured decisions)
- [x] Daily briefing (Presenter, cached, Claude-generated)
- [x] Memory extraction + approval flow + audit logging
- [x] Meeting prep workflow
- [x] OpenClaw plugin: 11 tools (command, brief, approve, tasks, search, meeting_prep, dashboard, approval_card, task_detail, ingest_event, heartbeat)

### Milestone 2: Intelligence Layer
- [x] Semantic memory search (pgvector + Voyage AI embeddings)
- [x] Preference extraction (Claude-powered)
- [x] Proactive event-driven planning (auto-plan on ingestion)
- [x] Context-aware importance scoring (entities + preferences)
- [x] Heartbeat cron (memory expiry, plan escalation)

### Milestone 3: User Experience
- [x] Canvas UI (dashboard, approval cards, task detail)
- [x] Slack connector + notifications (later removed — OpenClaw handles)
- [x] Voice + WhatsApp stubs (later removed — OpenClaw handles)

### Milestone 4: Hardening
- [x] Retry + idempotency, execution locks, dead-letter queues
- [x] Observability (tracing, correlation IDs, metrics)
- [x] Security (rate limiting, request size limits, CORS)

### Milestone 5: Ecosystem Alignment
- [x] Removed connectors, notification, voice (OpenClaw owns these)
- [x] Generic `/v1/events/ingest` + agent-driven ingestion model
- [x] OpenClawClient (wake, delegate, run_agent_turn)
- [x] Redis infrastructure (cache, locks, streams, rate limiting)

### Milestone 6: Production Deployment
- [x] AWS (Terraform), Bedrock, Caddy, systemd, Telegram
- [x] Deploy script, daily backups, security hardening
- [x] Live on `jarvis.brrdcast.in`

---

## Phase 1: Make It Real (Next)

**Goal**: The full Perceive → Understand → Plan → Act → Communicate loop runs continuously with real data. Not just services that exist — a system that actually works end-to-end.

### Milestone 7: Observation Loop

The agent needs to periodically read data and feed it to Jarvis. This is the "always watching" behavior.

- [ ] **Scheduled observation skill**: OpenClaw skill/cron that triggers agent to read Gmail, Calendar, GitHub and ingest via `jarvis_ingest_event`
- [ ] **Observation cadence config**: Define per-source polling intervals (email: 5min, calendar: 15min, github: 10min)
- [ ] **Smart batching**: Agent reads multiple items per observation, batches ingestion calls
- [ ] **Observation health**: Backend tracks last-seen timestamps per source; heartbeat flags stale sources
- [ ] **First real daily brief**: Morning cron → agent observes → backend generates brief → agent delivers via Telegram

### Milestone 8: End-to-End Acceptance

Prove the full loop works for real founder scenarios.

- [ ] **Scenario: Important email** — Email arrives → scored high → plan: draft reply → approval → agent sends
- [ ] **Scenario: Meeting prep** — Calendar event approaching → agent observes → backend generates prep card → delivered proactively
- [ ] **Scenario: GitHub PR** — PR opened on key repo → scored → added to briefing → user informed
- [ ] **Scenario: Follow-up needed** — Email sent 3 days ago, no reply → detected → added to brief as "follow up"
- [ ] **Scenario: Conflicting meeting** — Double-booked calendar → detected → flagged in brief with recommendation
- [ ] **Integration test suite**: Automated tests for each scenario with mocked OpenClaw agent

---

## Phase 2: Make It Smart

**Goal**: Jarvis doesn't just process events — it connects dots, anticipates needs, and gets smarter over time. This is where the "Iron Man's Jarvis" feeling starts.

### Milestone 9: Cross-Source Intelligence

- [ ] **Event correlation**: Email from person X + meeting with X tomorrow → auto-generate meeting context
- [ ] **Thread tracking**: Track email/Slack threads across time, detect stale conversations needing follow-up
- [ ] **Deadline detection**: Extract deadlines from events, track countdown, escalate in briefs
- [ ] **Project pulse**: Aggregate events per project → "Project X: 3 emails, 2 PRs, 1 meeting this week — trending hot"
- [ ] **People pulse**: Track interaction frequency per entity → "Haven't heard from investor Y in 2 weeks"

### Milestone 10: Learning from Behavior

- [ ] **Approval pattern learning**: Track what user approves/rejects → adjust importance scoring weights
- [ ] **Brief feedback loop**: Track which brief items user acts on → prioritize similar items higher
- [ ] **Preference refinement**: Auto-extract preferences from approval patterns ("user always approves calendar invites from team")
- [ ] **Scoring model v2**: Learned weights + rule-based signals → better importance/urgency scores
- [ ] **Memory consolidation**: Periodic merging of related memories, pruning low-confidence ones

### Milestone 11: Proactive Intelligence

- [ ] **Proactive nudges**: Backend detects actionable patterns → wakes agent with suggestions (not just briefs)
- [ ] **Preparation triggers**: Upcoming meeting in 30min with entity X → auto-generate and deliver prep
- [ ] **Follow-up reminders**: Sent email 3 days ago, no reply → proactive nudge in brief
- [ ] **Anomaly detection**: Unusual patterns (10x normal email volume, missed recurring meeting) → flag
- [ ] **Weekly digest**: Aggregate weekly patterns, highlight trends, suggest focus areas

---

## Phase 3: Make It Autonomous

**Goal**: Graduated trust. Jarvis earns autonomy by proving reliability. Move from "approve everything" to "Jarvis handles routine, flags exceptions."

### Milestone 12: Policy Modes

- [ ] **Configurable policy modes**: `lockdown` (approve all) → `approval_required` (default) → `suggest_only` → `full_auto`
- [ ] **Per-action-type policies**: Different trust levels for email send vs calendar create vs GitHub comment
- [ ] **Risk-based escalation**: Low-risk actions auto-execute, high-risk always require approval
- [ ] **Time-based policies**: Auto-approve during work hours, lockdown at night
- [ ] **Policy dashboard**: Show current mode, recent auto-executions, approval history

### Milestone 13: Trust Calibration

- [ ] **Trust score per action type**: Built from approval history (approved 50/50 email sends → trust = 1.0)
- [ ] **Auto-approve threshold**: Actions above trust threshold + below risk threshold → auto-execute
- [ ] **Escalation chains**: If agent delegation fails → retry → DLQ → escalate to user
- [ ] **Rollback capability**: For auto-executed actions, track what was done for potential undo
- [ ] **Trust reset**: User can reset trust scores, return to lockdown mode

---

## Phase 4: Make It Personal

**Goal**: Jarvis knows you deeply. Not just your calendar — your thinking patterns, communication style, relationship dynamics, and working rhythms.

### Milestone 14: Deep Personalization

- [ ] **Communication style profiles**: Learn user's writing style per context (formal for investors, casual for team)
- [ ] **Relationship graph enrichment**: Track relationship strength, last interaction, sentiment trends
- [ ] **Working rhythm model**: Learn when user is most productive, when they prefer meetings, break patterns
- [ ] **Context-aware drafting**: Drafts match user's style for the specific recipient and context
- [ ] **Priority model personalization**: User-specific importance weights (fundraising > hiring > ops)

### Milestone 15: Multi-Agent Specialization (via OpenClaw)

Leverage OpenClaw's native multi-agent routing — don't build custom routing.

- [ ] **Work agent**: Optimized for professional context (email, meetings, PRs, tasks)
- [ ] **Research agent**: Deep analysis mode (longer context, web search, document synthesis)
- [ ] **Quick agent**: Fast responses for routine queries (schedule check, status, approvals)
- [ ] **Agent-specific SOUL.md files**: Each agent gets different system prompt + tool access
- [ ] **Shared backend**: All agents talk to same Jarvis backend (world model, memory, planner)

---

## Phase 5: Scale & Reliability

### Milestone 16: Production Hardening

- [ ] **Monitoring SLOs**: Event latency < 2s, briefing < 5s, zero missed approvals, < 1% error rate
- [ ] **Alerting**: PagerDuty/Telegram alerts on SLO breaches
- [ ] **Load testing**: Simulate 1000 events/day, 50 plans/day, 10 briefs/day
- [ ] **Graceful degradation**: If Claude/Bedrock is down → queue events, serve cached briefs
- [ ] **Cost tracking**: Per-user Claude token usage, embedding costs, infrastructure costs

### Milestone 17: Multi-User (Future)

- [ ] **User isolation**: Per-user data partitioning, separate API keys
- [ ] **Team awareness**: Shared entity graph with private memories
- [ ] **Admin dashboard**: Usage, costs, audit trail per user
- [ ] **Onboarding flow**: New user setup, data source connection, preference calibration

---

## Dropped Items (OpenClaw Handles These)

These were originally planned but are **not needed** — OpenClaw provides them natively:

| Item | Why Dropped | OpenClaw Alternative |
|------|-------------|---------------------|
| OAuth integration for data sources | OpenClaw agent handles auth | `gog` (Google), `gh` (GitHub) tools |
| Multi-connector expansion (Notion, Drive) | OpenClaw plugin ecosystem | Community/custom plugins |
| Mobile companion app | Channels provide mobile access | Telegram, WhatsApp channels |
| Custom multi-agent routing | OpenClaw has native routing | `agentId` + `binding` config |
| Notification service | OpenClaw agent sends messages | `message` tool |
| Voice features | OpenClaw has native TTS/STT | Built-in voice support |
| Channel adapters | OpenClaw's core feature | Built-in + plugin channels |

---

## Success Metrics

| Metric | Target | Phase |
|--------|--------|-------|
| Daily brief delivered by 9am | 100% | Phase 1 |
| Event-to-brief latency | < 5 min | Phase 1 |
| Important email detected and planned | > 90% recall | Phase 2 |
| Meeting prep delivered 30min before | > 95% | Phase 2 |
| Auto-approved actions (after trust calibration) | > 60% of routine | Phase 3 |
| User acts on brief recommendation | > 40% | Phase 4 |
| Briefing satisfaction (user feedback) | > 4/5 | Phase 4 |
