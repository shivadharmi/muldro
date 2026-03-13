# Jarvis System Architecture

## Overview

Jarvis is a Personal AI Operating System. It runs a continuous intelligence loop:

```
Perceive → Understand → Update Model → Plan → Act → Communicate → repeat
```

The system is split into two halves:

- **OpenClaw Gateway** — the user-facing interaction runtime (chat, voice, Canvas, scheduling)
- **Jarvis Backend** — the intelligence engine (connectors, events, world model, memory, planning, execution)

## Component Map

```
┌─────────────────────────────────────────────────────┐
│               User Surfaces                          │
│  WhatsApp · Slack · Web UI · Voice · Canvas · CLI    │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               OpenClaw Gateway                       │
│                                                      │
│  Message routing (bindings) · Session management     │
│  Claude model turns · Tool dispatch                  │
│  Canvas UI · Cron scheduling                         │
│  jarvis-tools plugin (thin HTTP bridge)              │
└────────┬─────────────────────────┬──────────────────┘
         │ tool calls (HTTP)       │ webhooks (HTTP)
┌────────▼─────────────────────────▼──────────────────┐
│               Jarvis Backend (FastAPI)               │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ API      │ │Connector │ │ Event    │            │
│  │ Gateway  │ │ Service  │ │ Processor│            │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       │            │            │                    │
│  ┌────▼────────────▼────────────▼─────┐             │
│  │       Internal Event Bus            │             │
│  │  (Postgres queues / Redis streams)  │             │
│  └────┬────────────┬────────────┬─────┘             │
│       │            │            │                    │
│  ┌────▼────┐ ┌─────▼────┐ ┌────▼─────┐             │
│  │ World   │ │ Memory   │ │ Planner  │             │
│  │ Model   │ │ Service  │ │          │             │
│  └─────────┘ └──────────┘ └────┬─────┘             │
│                                 │                    │
│  ┌──────────┐ ┌──────────┐ ┌───▼──────┐            │
│  │ Governor │ │ Presenter│ │ Operator │            │
│  └──────────┘ └──────────┘ └──────────┘            │
│                                                      │
│  ┌────────────────────────────────────────────┐     │
│  │  Postgres · pgvector · Redis · S3          │     │
│  └────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

## Data Flow: Important Email

```
1. Gmail Pub/Sub → OpenClaw plugin HTTP route → Jarvis backend webhook endpoint
2. Gmail connector fetches new message, stores raw payload
3. Event processor normalizes, scores importance/urgency/confidence
4. World model identifies sender entity and project linkage
5. Memory service retrieves relevant preferences and relationship context
6. Planner produces structured task graph: draft_reply + request_approval
7. Governor evaluates policy → marks approval_required
8. Operator creates draft email artifact
9. Presenter generates approval prompt
10. Backend notifies OpenClaw via /hooks/wake
11. OpenClaw model calls jarvis_approve tool → presents to user
```

## Data Flow: Morning Brief

```
1. OpenClaw cron triggers at configured time
2. Model calls jarvis_brief tool
3. Backend daily briefing workflow:
   a. Fetch events since last briefing
   b. Group by project, people, deadlines
   c. Retrieve active goals and pending approvals
   d. Planner selects top priorities
   e. Presenter generates structured brief
4. Returns to OpenClaw model for natural language presentation
```

## Database Schema

### Core Tables

| Table | Purpose | Key Indexes |
|-------|---------|-------------|
| normalized_events | All ingested events | (user_id, occurred_at), (user_id, source, entity_id) |
| entities | People, projects, tasks, meetings | (user_id, entity_type, canonical_name) |
| entity_aliases | Email addresses, handles, etc. | (alias) |
| entity_relationships | Graph edges between entities | (from_entity_id), (to_entity_id) |
| memories | Long-term learned knowledge | (user_id, memory_type, status) |
| plans | Planner output / task graphs | (user_id, created_at) |
| plan_tasks | Individual tasks within a plan | (plan_id) |
| executions | Plan execution state | (user_id) |
| execution_task_runs | Per-task execution results | (execution_id) |
| approvals | Pending/decided approval items | (user_id, status, created_at) |
| briefings | Daily briefing snapshots | (user_id, briefing_date) |
| audit_logs | Every external action | (user_id) |
| connectors | Configured source integrations | (user_id) |
| connector_accounts | OAuth credentials + sync state | (connector_id) |

## Execution State Machine

```
detected → planned → policy_checked → awaiting_approval → approved → executing → completed
                                    → auto_execute     ─────────────────────────→ completed
                                    → blocked          ─────────────────────────→ cancelled
                                                        → rejected ────────────→ cancelled
                                                                                → failed
```

## Security Model

- **v1**: Single trusted user boundary per gateway
- **Trust layers**: OpenClaw gateway trusted → Jarvis backend trusted → External APIs scoped
- **Secrets**: Connector credentials encrypted at rest, never in model context
- **Approvals**: All external writes gated (no auto-send in v1)
- **Audit**: Full trail with event_id, plan_id, execution_id, approval_id correlation
- **Threats mitigated**: Prompt injection (content separation), duplicate webhooks (idempotency), stale plans (version checks), memory pollution (significance thresholds), over-automation (execution modes)
