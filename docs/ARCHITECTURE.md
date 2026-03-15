# Jarvis System Architecture

## Overview

Jarvis is a Personal AI Operating System. It runs a continuous intelligence loop:

```
Perceive -> Understand -> Update Model -> Plan -> Act -> Communicate -> repeat
```

The system is a unified intelligence backend with Telegram as the primary user surface:

- **Jarvis Backend** — the intelligence engine (event processing, world model, memory, planning, governance, execution tracking, briefings, audit, scheduling, Telegram delivery)

## Component Map

```
+-----------------------------------------------------+
|               User Surfaces                          |
|  Telegram Bot . REST API . MCP Tools                 |
+------------------------+----------------------------+
                         |
+------------------------v----------------------------+
|               Jarvis Backend (FastAPI)               |
|                                                      |
|  +----------+ +----------+ +----------+             |
|  | API      | | Event    | | Scheduler|             |
|  | Gateway  | | Processor| | Loop     |             |
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
|                           executes via               |
|                           Google API / GitHub API     |
|                           / Telegram Bot API         |
|                                                      |
|  +--------------------------------------------+     |
|  |  Postgres . pgvector . Redis . Audit Trail  |     |
|  +--------------------------------------------+     |
+------------------------------------------------------+
```

## Data Flow: Important Email

```
1. Scheduler triggers observation -> backend reads new emails via Google API
2. Events ingested via internal event processing pipeline
3. Event processor normalizes, scores importance/urgency/confidence
4. Callbacks fire:
   a. World model identifies sender entity and project linkage
   b. Memory service retrieves relevant preferences and relationship context
   c. Planner produces structured task graph: draft_reply + request_approval
5. Governor evaluates policy -> marks approval_required
6. Notification sent to user via Telegram
7. User approves via Telegram -> Operator executes email send via Google API
8. Operator records result, audit trail updated
```

## Data Flow: Morning Brief

```
1. Scheduler triggers at configured time (e.g., 9am daily)
2. Backend daily briefing workflow:
   a. Fetch events since last briefing
   b. Group by project, people, deadlines
   c. Retrieve active goals and pending approvals
   d. Fetch upcoming meetings
   e. Presenter generates structured brief via Claude
3. Backend delivers briefing to user via Telegram
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
detected -> planned -> policy_checked -> awaiting_approval -> approved -> executing -> completed
                                      -> auto_execute     --------------------------> completed
                                      -> blocked          --------------------------> cancelled
                                                           -> rejected --------------> cancelled
                                                                                    -> failed
```

## Infrastructure

| Component | Purpose |
|-----------|---------|
| PostgreSQL 17 | Primary data store, pgvector for embeddings |
| Redis 7 | Caching (briefings, entities), rate limiting, distributed locks, task streams |
| CallbackWorker | Background processor for async event callbacks (entity/memory/planning) |
| SchedulerLoop | Dynamic scheduling for observations, briefings, and maintenance |
| TelegramClient | Delivers briefings, notifications, and approval prompts to user |
| AWS Bedrock | Alternative model provider (Claude via AWS IAM, no API key needed) |
| Caddy | Reverse proxy with automatic TLS (production) |

## Security Model

- **v1**: Single trusted user boundary
- **Trust layers**: Jarvis backend trusted -> External APIs scoped
- **Approvals**: All external writes gated (no auto-send in v1)
- **Audit**: Full trail with event_id, plan_id, execution_id, approval_id correlation
- **Rate limiting**: Redis-backed sliding window (with in-memory fallback)
- **Request size limits**: Configurable max body size
- **Threats mitigated**: Prompt injection (content separation), duplicate events (idempotency keys), stale plans (TTL + heartbeat invalidation), memory pollution (significance thresholds), over-automation (execution modes + policy gates)
