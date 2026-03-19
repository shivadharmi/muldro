# Event Ingestion & Proactive Intelligence

## Event Processing Pipeline

```mermaid
sequenceDiagram
    participant S as Source (Gmail/Slack/GitHub)
    participant EP as EventProcessor
    participant C as Claude (Scoring)
    participant WM as WorldModel
    participant MS as MemoryService
    participant IS as InitiativeScorer
    participant TE as TriggerEngine
    participant PL as Planner
    participant NT as Notifier
    participant EB as EventBus

    S->>EP: RawEvent (source, type, payload)

    Note over EP: Normalize & Dedup
    EP->>EP: Generate idempotency_key (source:entity_id:event_type)
    EP->>EP: Check for duplicate (skip if exists)

    Note over EP,C: Score Event
    EP->>C: "Rate importance, urgency, confidence"
    C-->>EP: {importance: 0-1, urgency: 0-1, confidence: 0-1}
    EP->>EP: Store NormalizedEvent

    Note over EP,TE: Evaluate Triggers
    EP->>TE: Match against user-defined triggers
    alt Trigger matches
        TE->>NT: action=notify
        TE->>PL: action=plan
        TE->>NT: action=escalate (critical alert)
    end

    Note over EP,IS: Proactive Intelligence
    EP->>IS: Score initiative (composite formula)
    IS-->>EP: {score, should_plan, should_notify}

    alt score >= 0.70
        EP->>PL: Auto-create plan
    else score >= 0.50
        EP->>NT: Proactive notification
    end

    Note over EP,EB: Downstream Processing
    EP->>EB: Publish to event stream
    EB->>WM: entity_extractor consumer
    EB->>MS: memory_extractor consumer
    EB->>PL: planner consumer (importance >= 0.7)
    EB->>TE: trigger_evaluator consumer
```

## Event Scoring

Events are scored via Claude with three dimensions:

| Dimension | Range | Signals |
|-----------|-------|---------|
| **Importance** | 0.0 - 1.0 | From priority person, related to active project, financial impact, deadline |
| **Urgency** | 0.0 - 1.0 | Time-sensitive, requires immediate response, blocking others |
| **Confidence** | 0.0 - 1.0 | How certain the scoring is (data quality, context available) |

Default scores (on Claude error): `{importance: 0.3, urgency: 0.2, confidence: 0.5}`

Events with importance < 0.4 are skipped for planning.

## Initiative Scoring

The `InitiativeScorer` determines when Jarvis should proactively act without explicit user request.

### Composite Formula

```
score = 0.30 * importance
      + 0.25 * urgency
      + 0.20 * goal_relevance
      + 0.15 * entity_significance
      + 0.10 * novelty
```

### Signal Boosts

| Boost | Value | Condition |
|-------|-------|-----------|
| Priority person | +0.15 | Event actor is a high-importance entity |
| Contains deadline | +0.10 | Event mentions a deadline or due date |

Final score is capped at 1.0.

### Signal Computation

| Signal | How It's Computed |
|--------|-------------------|
| `importance` | From event scoring (Claude) |
| `urgency` | From event scoring (Claude) |
| `goal_relevance` | Keyword overlap between event title/summary and active user goals |
| `entity_significance` | Importance score of actors in event (via WorldModel.find_entity) |
| `novelty` | 1.0 minus top similarity if no related memories found; inverted relevance otherwise |

### Thresholds

| Threshold | Value | Action |
|-----------|-------|--------|
| `auto_plan_threshold` | 0.70 | Auto-create plan via Planner |
| `notify_threshold` | 0.50 | Send proactive notification to user |

## Trigger System

Users can define triggers that fire reactive rules when events match conditions.

### Trigger Conditions

| Condition | Description |
|-----------|-------------|
| `event_type` | Match specific event type |
| `source` | Match source (gmail, slack, github) |
| `entity_type` | Match entity type (person, project, etc.) |
| `importance_threshold` | Fire only if importance >= threshold |
| `keyword_match` | Text pattern match on event content |
| `cooldown` | Minimum time between firings |

### Trigger Actions

| Action | Effect |
|--------|--------|
| `notify` | Send notification via Notifier |
| `plan` | Create plan via Planner |
| `escalate` | Send critical alert to all surfaces |
| `procedure` | Execute a predefined procedure |

### Trigger Lifecycle

```
active -> evaluating -> triggered -> active (with cooldown)
                    \-> active (no match)
```

Triggers track `fire_count`, `last_fired_at`, and respect `cooldown_until`.

## Domain Events

The system publishes 27 domain event types via Redis-backed EventBus. All events include a `workspace_id` for multi-tenant scoping.

| Category | Event Types |
|----------|-------------|
| **Execution** | `run.started`, `run.completed`, `run.failed`, `run.cancelled` |
| **Steps** | `step.started`, `step.completed`, `step.failed`, `step.skipped` |
| **Planning** | `plan.created`, `plan.approved`, `plan.rejected` |
| **Approvals** | `approval.requested`, `approval.approved`, `approval.rejected` |
| **Tools** | `tool.started`, `tool.completed`, `tool.failed` |
| **Connectors** | `connector.synced`, `connector.error` |
| **Memory** | `memory.created`, `memory.updated` |
| **Entities** | `entity.created`, `entity.updated` |
| **Triggers** | `trigger.evaluated` |
| **Notifications** | `notification.sent`, `notification.delivered` |
| **UI** | `surface.updated` |

### Event Schema

```python
class DomainEvent(BaseModel):
    event_type: str           # e.g., "run.completed"
    user_id: str
    workspace_id: str         # Workspace scope for multi-tenant isolation
    payload: dict[str, Any]
    trace_id: str | None
    timestamp: datetime       # UTC
```

## Worker Consumer Groups

Background workers consume events from Redis streams:

| Consumer Group | Handler | Action |
|---------------|---------|--------|
| `entity_extractor` | WorldModel.extract_from_event() | Extract entities + relationships |
| `memory_extractor` | MemoryService.extract_and_store() | Extract memories with entity linking |
| `planner` | Planner.plan_for_event() | Auto-plan for high-importance events |
| `trigger_evaluator` | TriggerEngine.evaluate() | Match triggers against events |
