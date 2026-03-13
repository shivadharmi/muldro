# Jarvis System Architecture

## Overview

Jarvis is a Personal AI Operating System. It runs a continuous intelligence loop:

```
Perceive → Understand → Update Model → Plan → Act → Communicate → repeat
```

The system is split into two halves:

- **OpenClaw Gateway + Agent** — the user-facing interaction runtime and execution surface (chat, voice, Canvas, scheduling, data access via gog/gh/message)
- **Jarvis Backend** — the intelligence engine (event processing, world model, memory, planning, governance, execution tracking, briefings, audit)

## Component Map

```
+-----------------------------------------------------+
|               User Surfaces                          |
|  WhatsApp . Slack . Web UI . Voice . Canvas . CLI    |
+------------------------+----------------------------+
                         |
+------------------------v----------------------------+
|               OpenClaw Gateway                       |
|                                                      |
|  Message routing (bindings) . Session management     |
|  Claude model turns . Tool dispatch                  |
|  Canvas UI . Cron scheduling                         |
|  jarvis-tools plugin (thin HTTP bridge)              |
+--------+---------------------------+----------------+
         | tool calls (HTTP)         | /hooks/wake & /hooks/agent
+--------v---------------------------v----------------+
|               Jarvis Backend (FastAPI)               |
|                                                      |
|  +----------+ +----------+ +----------+             |
|  | API      | | Event    | | OpenClaw |             |
|  | Gateway  | | Processor| | Client   |             |
|  +----+-----+ +----+-----+ +----+-----+             |
|       |            |            |                    |
|  +----v------------v------------v------+             |
|  |       Internal Event Bus            |             |
|  |  (Redis streams / Postgres queues)  |             |
|  +----+------------+------------+------+             |
|       |            |            |                    |
|  +----v----+ +-----v----+ +----v-----+              |
|  | World   | | Memory   | | Planner  |              |
|  | Model   | | Service  | |          |              |
|  +---------+ +----------+ +----+-----+              |
|                                |                     |
|  +----------+ +----------+ +--v-------+             |
|  | Governor | | Presenter| | Operator |             |
|  +----------+ +----------+ +----+-----+             |
|                                  |                   |
|                           delegates to               |
|                           OpenClaw agent             |
|                           (gog/gh/message)           |
|                                                      |
|  +--------------------------------------------+     |
|  |  Postgres . pgvector . Redis . Audit Trail  |     |
|  +--------------------------------------------+     |
+------------------------------------------------------+
```

## Data Flow: Important Email

```
1. OpenClaw agent reads new emails via gog gmail
2. Agent calls jarvis_ingest_event → POST /v1/events/ingest
3. Event processor normalizes, scores importance/urgency/confidence
4. Callbacks fire:
   a. World model identifies sender entity and project linkage
   b. Memory service retrieves relevant preferences and relationship context
   c. Planner produces structured task graph: draft_reply + request_approval
5. Governor evaluates policy → marks approval_required
6. Governor wakes OpenClaw agent via /hooks/wake
7. Agent presents approval prompt to user (jarvis_approval_card)
8. User approves → Operator delegates email send to agent via /hooks/agent
9. Agent sends email via gog gmail send
10. Operator records result, audit trail updated
```

## Data Flow: Morning Brief

```
1. OpenClaw cron triggers at configured time
2. Agent calls jarvis_brief tool
3. Backend daily briefing workflow:
   a. Fetch events since last briefing
   b. Group by project, people, deadlines
   c. Retrieve active goals and pending approvals
   d. Fetch upcoming meetings
   e. Presenter generates structured brief via Claude
4. Backend wakes agent via /hooks/wake to deliver briefing
5. Agent presents briefing to user via chat/Canvas
```

## Database Schema

### Core Tables

| Table | Purpose | Key Indexes |
|-------|---------|-------------|
| normalized_events | All ingested events | (user_id, occurred_at), (user_id, source, entity_id) |
| entities | People, projects, tasks, meetings | (user_id, entity_type, canonical_name) |
| entity_aliases | Email addresses, handles, etc. | (alias) |
| entity_relationships | Graph edges between entities | (from_entity_id), (to_entity_id) |
| memories | Long-term learned knowledge (pgvector) | (user_id, memory_type, status), HNSW on embedding |
| plans | Planner output / task graphs | (user_id, created_at) |
| plan_tasks | Individual tasks within a plan | (plan_id) |
| executions | Plan execution state | (user_id) |
| execution_task_runs | Per-task execution results | (execution_id) |
| approvals | Pending/decided approval items | (user_id, status, created_at) |
| briefings | Daily briefing snapshots | (user_id, briefing_date) |
| audit_logs | Every external action | (user_id) |
| dead_letter_queue | Failed operations for retry | (status, created_at) |

## Execution State Machine

```
detected → planned → policy_checked → awaiting_approval → approved → executing → completed
                                    → auto_execute     ─────────────────────────→ completed
                                    → blocked          ─────────────────────────→ cancelled
                                                        → rejected ────────────→ cancelled
                                                                                → failed
```

## Infrastructure

| Component | Purpose |
|-----------|---------|
| PostgreSQL 17 | Primary data store, pgvector for embeddings |
| Redis 7 | Caching (briefings, entities), rate limiting, distributed locks, task streams |
| CallbackWorker | Background processor for async event callbacks (entity/memory/planning) |
| OpenClawClient | HTTP bridge to OpenClaw gateway (/hooks/wake, /hooks/agent) |

## Security Model

- **v1**: Single trusted user boundary per gateway
- **Trust layers**: OpenClaw gateway trusted → Jarvis backend trusted → External APIs scoped
- **Approvals**: All external writes gated (no auto-send in v1)
- **Audit**: Full trail with event_id, plan_id, execution_id, approval_id correlation
- **Rate limiting**: Redis-backed sliding window (with in-memory fallback)
- **Request size limits**: Configurable max body size
- **Threats mitigated**: Prompt injection (content separation), duplicate events (idempotency keys), stale plans (TTL + heartbeat invalidation), memory pollution (significance thresholds), over-automation (execution modes + policy gates)
