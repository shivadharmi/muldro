Below is the **implementation pack** for Jarvis as a full AI OS.

It is structured so you can directly start coding.

# 1. Monorepo skeleton

```text
jarvis/
  README.md
  .env.example
  docker-compose.yml
  pnpm-workspace.yaml
  pyproject.toml

  apps/
    web/
      app/
      components/
      lib/
      hooks/
      styles/
      package.json
      next.config.ts

    api/
      src/
        main.py
        config.py
        dependencies.py
        routers/
        middleware/
        schemas/
      pyproject.toml

    runtime/
      src/
        main.py
        config.py
        workers/
        orchestrator/
        planners/
        executors/
        verifiers/
        memory/
      pyproject.toml

    workers/
      src/
        main.py
        jobs/
        consumers/
        producers/
      pyproject.toml

    browser/
      src/
        main.py
        sessions/
        actions/
        extractors/
        replays/
      pyproject.toml

  packages/
    core/
      python/
        jarvis_core/
          events/
          enums/
          ids/
          logging/
          policies/
          utils/
      typescript/
        src/
          events/
          types/
          schemas/

    schemas/
      json/
        event.schema.json
        task.schema.json
        run.schema.json
        approval.schema.json
        ui.schema.json

    task-engine/
      src/
        domain/
        application/
        infra/

    agent-runtime/
      src/
        contracts/
        context/
        planner/
        executor/
        verifier/
        communicator/
        ui_composer/

    memory/
      src/
        semantic/
        episodic/
        procedural/
        preferences/
        artifact/

    retrieval/
      src/
        pipelines/
        rankers/
        compressors/

    graph/
      src/
        entities/
        edges/
        resolvers/

    tool-gateway/
      src/
        registry/
        executor/
        validators/
        sandbox/

    connector-sdk/
      src/
        base/
        oauth/
        sync/
        actions/
        events/
        normalizers/

    ui-schema/
      src/
        dsl/
        validators/
        render_mappers/

    notifications/
      src/
        channels/
        scoring/
        digests/

    observability/
      src/
        tracing/
        metrics/
        evals/

    auth/
      src/
        sessions/
        tokens/
        permissions/

  services/
    gmail/
      src/
    calendar/
      src/
    drive/
      src/
    search/
      src/
    slack/
      src/
    internal_api/
      src/

  infra/
    docker/
      postgres/
      redis/
      opensearch/
    terraform/
    k8s/
    scripts/

  docs/
    architecture/
    schemas/
    runbooks/
    prd/
```

---

# 2. Core domain model

## Main objects

### User

Who uses Jarvis.

### Workspace

Shared operating boundary.

### Goal

Long-lived objective.

### Task

Unit of work.

### Run

Execution instance of a task.

### Step

Atomic execution unit inside a run.

### Artifact

Produced or consumed asset.

### Entity

Graph object in the world model.

### Event

Everything important that happens.

### ApprovalRequest

Human checkpoint for risky operations.

### Watcher

Persistent monitoring rule.

---

# 3. Postgres schema starter

## Core tables

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  name TEXT,
  timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  type TEXT NOT NULL DEFAULT 'personal',
  settings_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, user_id)
);

CREATE TABLE goals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_user_id UUID REFERENCES users(id),
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  priority INT NOT NULL DEFAULT 5,
  success_criteria_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id),
  goal_id UUID REFERENCES goals(id),
  parent_task_id UUID REFERENCES tasks(id),
  title TEXT NOT NULL,
  description TEXT,
  task_type TEXT NOT NULL,
  source TEXT NOT NULL,
  priority INT NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'queued',
  due_at TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_dependencies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  dependency_type TEXT NOT NULL DEFAULT 'hard',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(task_id, depends_on_task_id)
);

CREATE TABLE task_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'created',
  runtime_version TEXT NOT NULL,
  planner_version TEXT,
  verifier_version TEXT,
  context_pack_json JSONB,
  checkpoint_ref TEXT,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  error_summary TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
  step_order INT NOT NULL,
  step_type TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  retry_count INT NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id, step_order)
);

CREATE TABLE approval_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  run_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
  step_id UUID REFERENCES task_steps(id) ON DELETE SET NULL,
  approval_type TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  action_payload_json JSONB NOT NULL,
  preview_artifact_id UUID,
  status TEXT NOT NULL DEFAULT 'pending',
  requested_by UUID REFERENCES users(id),
  approved_by UUID REFERENCES users(id),
  rejected_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  producer TEXT NOT NULL,
  entity_type TEXT,
  entity_id UUID,
  payload_json JSONB NOT NULL,
  correlation_id UUID,
  causation_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  run_id UUID REFERENCES task_runs(id) ON DELETE SET NULL,
  artifact_type TEXT NOT NULL,
  title TEXT NOT NULL,
  uri TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  source TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entity_edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  from_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  to_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  edge_type TEXT NOT NULL,
  weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
  evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  memory_type TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id UUID,
  content TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  salience_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  embedding VECTOR(1536),
  source_event_id UUID REFERENCES events(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ
);

CREATE TABLE watchers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  source_config_json JSONB NOT NULL,
  condition_json JSONB NOT NULL,
  action_plan_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  cooldown_until TIMESTAMPTZ,
  last_evaluated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'pending',
  sent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

# 4. Core enums

## Task status

```python
TASK_STATUS = [
    "draft",
    "queued",
    "planning",
    "ready",
    "running",
    "waiting_for_data",
    "waiting_for_external_event",
    "waiting_for_user",
    "waiting_for_approval",
    "partially_completed",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "archived",
]
```

## Run status

```python
RUN_STATUS = [
    "created",
    "starting",
    "retrieving_context",
    "planning",
    "executing",
    "verifying",
    "generating_output",
    "awaiting_approval",
    "awaiting_resume",
    "awaiting_external_callback",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "cancelled",
    "timed_out",
]
```

## Step status

```python
STEP_STATUS = [
    "pending",
    "running",
    "success",
    "failed",
    "retrying",
    "skipped",
    "waiting",
    "blocked",
    "aborted",
]
```

## Risk level

```python
RISK_LEVEL = ["low", "medium", "high", "critical"]
```

---

# 5. Event schema

Every meaningful state change emits an event.

## Base event

```json
{
  "id": "uuid",
  "workspace_id": "uuid",
  "event_type": "task.created",
  "producer": "api",
  "entity_type": "task",
  "entity_id": "uuid",
  "payload": {},
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "created_at": "2026-03-16T12:00:00Z"
}
```

## Event families

```text
user.*
goal.*
task.*
run.*
step.*
approval.*
tool.*
connector.*
memory.*
entity.*
watcher.*
notification.*
ui.*
browser.*
system.*
```

## Important concrete events

```text
task.created
task.status_changed
run.started
run.completed
run.failed
step.started
step.succeeded
step.failed
approval.requested
approval.approved
approval.rejected
tool.execution_requested
tool.execution_succeeded
tool.execution_failed
connector.sync_completed
connector.webhook_received
memory.item_created
watcher.matched
notification.sent
ui.view_generated
browser.action_executed
```

---

# 6. JSON schemas you should define first

Create these files in `packages/schemas/json/`.

## `task.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Task",
  "type": "object",
  "required": ["id", "workspace_id", "title", "task_type", "source", "status"],
  "properties": {
    "id": { "type": "string" },
    "workspace_id": { "type": "string" },
    "user_id": { "type": ["string", "null"] },
    "goal_id": { "type": ["string", "null"] },
    "title": { "type": "string" },
    "description": { "type": ["string", "null"] },
    "task_type": { "type": "string" },
    "source": { "type": "string" },
    "priority": { "type": "integer" },
    "status": { "type": "string" },
    "metadata_json": { "type": "object" }
  }
}
```

## `run.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TaskRun",
  "type": "object",
  "required": ["id", "task_id", "status", "runtime_version"],
  "properties": {
    "id": { "type": "string" },
    "task_id": { "type": "string" },
    "status": { "type": "string" },
    "runtime_version": { "type": "string" },
    "planner_version": { "type": ["string", "null"] },
    "verifier_version": { "type": ["string", "null"] },
    "context_pack_json": { "type": ["object", "null"] },
    "checkpoint_ref": { "type": ["string", "null"] }
  }
}
```

## `approval.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ApprovalRequest",
  "type": "object",
  "required": ["id", "run_id", "approval_type", "risk_level", "title", "summary", "status"],
  "properties": {
    "id": { "type": "string" },
    "run_id": { "type": "string" },
    "step_id": { "type": ["string", "null"] },
    "approval_type": { "type": "string" },
    "risk_level": { "type": "string" },
    "title": { "type": "string" },
    "summary": { "type": "string" },
    "action_payload_json": { "type": "object" },
    "status": { "type": "string" }
  }
}
```

## `ui.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UIView",
  "type": "object",
  "required": ["view_type", "title"],
  "properties": {
    "view_type": { "type": "string" },
    "title": { "type": "string" },
    "layout": { "type": ["string", "null"] },
    "sections": {
      "type": "array",
      "items": { "type": "object" }
    },
    "actions": {
      "type": "array",
      "items": { "type": "object" }
    },
    "data": { "type": ["object", "null"] }
  }
}
```

---

# 7. Service boundaries

## API service

Responsible for:

* auth/session validation
* user-facing REST endpoints
* command intake
* task creation
* approval response endpoints
* notification listing
* realtime subscription bootstrap

## Runtime service

Responsible for:

* task run orchestration
* planner/executor/verifier lifecycle
* checkpointing
* run state mutation
* UI generation
* memory writeback initiation

## Worker service

Responsible for:

* background jobs
* watcher evaluation
* scheduled briefings
* indexing
* async notifications
* retry jobs

## Browser service

Responsible for:

* browser sessions
* safe interactions
* screenshots
* page extraction
* replay logs

## Connector services

Responsible for:

* sync third-party data
* normalize into internal records
* perform provider-specific actions
* receive webhooks

---

# 8. Core API routes

## User-facing routes

### Tasks

```text
POST   /v1/tasks
GET    /v1/tasks
GET    /v1/tasks/{task_id}
POST   /v1/tasks/{task_id}/start
POST   /v1/tasks/{task_id}/cancel
POST   /v1/tasks/{task_id}/resume
```

### Runs

```text
GET    /v1/runs/{run_id}
GET    /v1/runs/{run_id}/steps
GET    /v1/runs/{run_id}/trace
GET    /v1/runs/{run_id}/artifacts
```

### Approvals

```text
GET    /v1/approvals
GET    /v1/approvals/{approval_id}
POST   /v1/approvals/{approval_id}/approve
POST   /v1/approvals/{approval_id}/reject
POST   /v1/approvals/{approval_id}/edit
```

### Watchers

```text
POST   /v1/watchers
GET    /v1/watchers
PATCH  /v1/watchers/{watcher_id}
POST   /v1/watchers/{watcher_id}/disable
POST   /v1/watchers/{watcher_id}/enable
```

### Briefings

```text
GET    /v1/briefings/today
GET    /v1/briefings/goal/{goal_id}
```

### Memory / entities

```text
GET    /v1/entities/{entity_id}
GET    /v1/entities
GET    /v1/memory/search
GET    /v1/goals
POST   /v1/goals
```

### Connectors

```text
GET    /v1/connectors
POST   /v1/connectors/{connector}/authorize
POST   /v1/connectors/{connector}/sync
GET    /v1/connectors/{connector}/health
```

### Realtime

```text
GET    /v1/realtime/events
GET    /v1/realtime/runs/{run_id}
```

---

# 9. Runtime contracts

## Normalized intent contract

```python
from pydantic import BaseModel
from typing import Literal, Optional, List

class NormalizedIntent(BaseModel):
    raw_input: str
    mode: Literal["chat_reply", "task_create", "watcher_create", "goal_update", "approval_response"]
    task_type: Optional[str] = None
    urgency: int = 5
    entities: List[str] = []
    desired_outputs: List[str] = []
```

## Context pack contract

```python
from pydantic import BaseModel
from typing import Any

class ContextPack(BaseModel):
    task_summary: dict[str, Any]
    goals: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    related_runs: list[dict[str, Any]] = []
    procedures: list[dict[str, Any]] = []
    preferences: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    constraints: list[str] = []
    tool_options: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
```

## Plan contract

```python
from pydantic import BaseModel, Field
from typing import Optional, List

class PlanStep(BaseModel):
    id: str
    name: str
    step_type: str
    tool_name: Optional[str] = None
    requires_approval: bool = False
    success_criteria: List[str] = Field(default_factory=list)

class ExecutionPlan(BaseModel):
    objective: str
    steps: List[PlanStep]
    success_conditions: List[str]
    fallback_strategy: Optional[str] = None
```

## Step result contract

```python
class StepResult(BaseModel):
    step_id: str
    status: str
    output: dict
    artifacts: list[dict] = []
    warnings: list[str] = []
    error: Optional[str] = None
```

---

# 10. Task engine interfaces

## Task repository

```python
class TaskRepository:
    async def create_task(self, payload: dict) -> dict: ...
    async def get_task(self, task_id: str) -> dict | None: ...
    async def update_status(self, task_id: str, status: str) -> None: ...
    async def list_tasks(self, workspace_id: str, limit: int = 50) -> list[dict]: ...
```

## Run repository

```python
class RunRepository:
    async def create_run(self, task_id: str, runtime_version: str) -> dict: ...
    async def get_run(self, run_id: str) -> dict | None: ...
    async def update_run_status(self, run_id: str, status: str) -> None: ...
    async def save_context_pack(self, run_id: str, context_pack: dict) -> None: ...
```

## Step repository

```python
class StepRepository:
    async def create_steps(self, run_id: str, steps: list[dict]) -> None: ...
    async def mark_started(self, step_id: str) -> None: ...
    async def mark_success(self, step_id: str, output: dict) -> None: ...
    async def mark_failure(self, step_id: str, error: str) -> None: ...
```

---

# 11. Orchestrator flow

## Main run lifecycle

```text
1. Create run
2. Set run -> retrieving_context
3. Build context pack
4. Set run -> planning
5. Generate execution plan
6. Persist steps
7. Set run -> executing
8. Execute steps sequentially or in DAG order
9. Pause if approval needed
10. Resume after approval
11. Set run -> verifying
12. Generate final artifacts + UI
13. Write memory candidates
14. Mark run succeeded / failed
15. Emit notifications if needed
```

## Pseudocode

```python
async def execute_run(task_id: str) -> str:
    run = await run_repo.create_run(task_id=task_id, runtime_version="0.1.0")
    await event_bus.publish("run.started", {"run_id": run["id"], "task_id": task_id})

    await run_repo.update_run_status(run["id"], "retrieving_context")
    context_pack = await context_builder.build(task_id=task_id, run_id=run["id"])
    await run_repo.save_context_pack(run["id"], context_pack.model_dump())

    await run_repo.update_run_status(run["id"], "planning")
    plan = await planner.generate_plan(task_id=task_id, context_pack=context_pack)
    await step_repo.create_steps(run["id"], [s.model_dump() for s in plan.steps])

    await run_repo.update_run_status(run["id"], "executing")
    for step in plan.steps:
        result = await executor.execute_step(run["id"], step, context_pack)
        if result.status == "awaiting_approval":
            await run_repo.update_run_status(run["id"], "awaiting_approval")
            return run["id"]
        if result.status == "failed":
            await verifier.handle_step_failure(run["id"], step, result)

    await run_repo.update_run_status(run["id"], "verifying")
    verdict = await verifier.verify_run(run["id"], plan.success_conditions)

    await run_repo.update_run_status(run["id"], "generating_output")
    ui_view = await ui_composer.compose(run["id"], verdict)
    await artifact_service.persist_ui_view(run["id"], ui_view)

    await memory_orchestrator.writeback(run["id"])
    await run_repo.update_run_status(run["id"], "succeeded")
    await event_bus.publish("run.completed", {"run_id": run["id"]})
    return run["id"]
```

---

# 12. Planner design

The planner should not be a freeform chain. It should output structured plans only.

## Planner inputs

* task
* context pack
* tool registry subset
* workspace policies
* task type playbook

## Planner outputs

* plan objective
* ordered steps
* approval gates
* expected artifacts
* success conditions
* fallback strategy

## Task playbooks to define first

```text
today_briefing
meeting_prep
inbox_triage
research_report
email_draft
watcher_setup
browser_research
goal_review
```

Each playbook should specify:

* usual retrieval sources
* preferred tools
* likely outputs
* approval patterns
* verification rules

---

# 13. Executor design

## Executor responsibilities

* validate step input
* resolve tool
* run policy pre-check
* request approval if needed
* execute tool
* validate output schema
* persist artifacts
* emit events

## Executor return states

```text
success
failed
retryable_failed
awaiting_approval
waiting_external
skipped
```

## Tool execution request contract

```python
class ToolExecutionRequest(BaseModel):
    tool_name: str
    workspace_id: str
    run_id: str
    step_id: str
    arguments: dict
    dry_run: bool = False
```

---

# 14. Policy engine

## Policy decisions

```python
class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool
    risk_level: str
    reason: str
```

## Example policy rules

```python
POLICY_RULES = {
    "send_email": {"risk": "high", "approval": True},
    "create_draft": {"risk": "medium", "approval": False},
    "delete_file": {"risk": "critical", "approval": True},
    "browser_submit_form": {"risk": "high", "approval": True},
    "calendar_create_event": {"risk": "medium", "approval": False},
}
```

## Decision logic

Inputs:

* tool risk level
* connector type
* external side effect or not
* user preference
* workspace policy
* action target
* confidence

---

# 15. Tool gateway design

## Tool definition model

```python
class ToolDefinition(BaseModel):
    name: str
    version: str
    category: str
    description: str
    input_schema: dict
    output_schema: dict
    risk_level: str
    requires_approval: bool
    timeout_seconds: int = 30
    idempotent: bool = False
```

## Tool registry

```python
class ToolRegistry:
    def register(self, tool: ToolDefinition) -> None: ...
    def get(self, tool_name: str) -> ToolDefinition: ...
    def list_for_task_type(self, task_type: str) -> list[ToolDefinition]: ...
```

## First tools to implement

```text
search_web
get_calendar_events
get_email_threads
create_email_draft
send_email
get_docs
summarize_artifacts
create_task
create_watcher
browser_open_page
browser_extract_page
browser_click
browser_fill_form
browser_submit
```

---

# 16. Connector framework

## Base connector interface

```python
class BaseConnector:
    name: str

    async def authorize(self, workspace_id: str, payload: dict) -> dict: ...
    async def sync(self, workspace_id: str) -> dict: ...
    async def health(self, workspace_id: str) -> dict: ...
    async def execute_action(self, action_name: str, payload: dict) -> dict: ...
```

## Connector normalization output examples

### Email message

```json
{
  "object_type": "message",
  "external_id": "gmail_msg_123",
  "thread_id": "gmail_thread_789",
  "subject": "Investor follow-up",
  "from": {"name": "A", "email": "a@example.com"},
  "to": [{"name": "B", "email": "b@example.com"}],
  "body_text": "...",
  "received_at": "2026-03-16T10:00:00Z"
}
```

### Calendar event

```json
{
  "object_type": "meeting",
  "external_id": "gcal_evt_123",
  "title": "Investor Sync",
  "start_at": "2026-03-16T15:00:00Z",
  "end_at": "2026-03-16T15:30:00Z",
  "attendees": [],
  "description": "..."
}
```

---

# 17. Memory architecture implementation

## Memory types

```text
working
episodic
semantic
procedural
preference
artifact
```

## Writeback pipeline

```text
1. candidate extraction
2. duplicate check
3. contradiction check
4. salience scoring
5. confidence assignment
6. persistence
7. optional embedding generation
```

## Memory candidate contract

```python
class MemoryCandidate(BaseModel):
    memory_type: str
    subject_type: str
    subject_id: str | None
    content: str
    salience_score: float
    confidence_score: float
    metadata: dict = {}
```

## Retrieval pipeline

```text
task -> entities -> recent episodes -> related artifacts -> preferences -> procedures -> rank -> compress
```

## Ranking formula

Use a weighted score like:

```text
score =
0.35 * relevance +
0.20 * recency +
0.15 * salience +
0.10 * confidence +
0.10 * entity_overlap +
0.10 * prior_usefulness
```

---

# 18. Context graph implementation

## Entity types to support first

```text
person
company
project
goal
task
meeting
thread
message
file
document
website
tool
watch_target
```

## Edge types to support first

```text
owns
related_to
mentioned_in
attends
sent_by
attached_to
depends_on
generated_from
linked_to_goal
monitors
```

## Entity resolver responsibilities

* dedupe entities
* map aliases
* merge attributes
* attach evidence
* update confidence

---

# 19. UI schema DSL

## Core view types

```text
chat_thread
briefing
task_detail
timeline
approval_panel
research_report
table
entity_card
kanban
form
command_palette
trace_view
dashboard
meeting_prep
inbox_triage
```

## Example `meeting_prep` schema

```json
{
  "view_type": "meeting_prep",
  "title": "Investor Meeting Prep",
  "layout": "stack",
  "sections": [
    {
      "type": "summary",
      "title": "Executive Summary",
      "content": "You are meeting X at 3 PM. Main objective is ..."
    },
    {
      "type": "list",
      "title": "Talking Points",
      "items": ["...", "..."]
    },
    {
      "type": "table",
      "title": "Recent Interactions",
      "columns": ["Date", "Type", "Summary"],
      "rows": []
    }
  ],
  "actions": [
    {"id": "draft_followup", "label": "Draft Follow-up"},
    {"id": "open_trace", "label": "Open Trace"}
  ]
}
```

## Frontend renderer map

```typescript
const viewRegistry = {
  briefing: BriefingView,
  task_detail: TaskDetailView,
  approval_panel: ApprovalPanelView,
  research_report: ResearchReportView,
  trace_view: TraceView,
  meeting_prep: MeetingPrepView,
  inbox_triage: InboxTriageView,
};
```

---

# 20. Realtime design

## Use SSE first

Stream:

* task status updates
* run status updates
* step updates
* approval requests
* UI view updates
* new notifications

## SSE event shape

```json
{
  "type": "step.succeeded",
  "run_id": "uuid",
  "step_id": "uuid",
  "payload": {}
}
```

---

# 21. Browser subsystem starter design

## Browser session table

```sql
CREATE TABLE browser_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  run_id UUID REFERENCES task_runs(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  current_url TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ
);
```

## Browser action log table

```sql
CREATE TABLE browser_action_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES browser_sessions(id) ON DELETE CASCADE,
  action_type TEXT NOT NULL,
  target TEXT,
  input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  screenshot_uri TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Browser tools to start with

```text
browser_open
browser_snapshot
browser_extract_text
browser_click
browser_type
browser_select
browser_wait_for
browser_submit
browser_take_screenshot
```

---

# 22. Watcher system

## Watcher contract

```python
class WatcherDefinition(BaseModel):
    title: str
    trigger_type: str
    source_config: dict
    condition: dict
    action_plan: dict
    cooldown_minutes: int = 30
```

## Watcher evaluation loop

```python
async def evaluate_watcher(watcher_id: str, event: dict) -> None:
    watcher = await watcher_repo.get(watcher_id)
    if not matcher.matches(event, watcher["condition_json"]):
        return

    if cooldown.is_active(watcher):
        return

    task = await task_service.create_from_watcher(watcher, event)
    await notification_service.notify_watcher_match(watcher, task)
```

## First watcher types

```text
important_email_reply
calendar_change
document_change
website_change
task_stalled
goal_inactive
```

---

# 23. Notifications

## Notification priority score

```text
priority_score =
0.30 * urgency +
0.25 * goal_relevance +
0.20 * novelty +
0.15 * confidence +
0.10 * user_interruptibility
```

## Channels

```text
in_app
push
email
slack
```

## Examples

* approval required
* important investor reply
* briefing ready
* task blocked
* run failed
* page change detected

---

# 24. Observability

## Tables to add later

```sql
CREATE TABLE traces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID REFERENCES task_runs(id) ON DELETE CASCADE,
  trace_type TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID REFERENCES task_runs(id) ON DELETE SET NULL,
  step_id UUID REFERENCES task_steps(id) ON DELETE SET NULL,
  model_name TEXT NOT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  latency_ms INT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12,6),
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Metrics to track first

```text
run_success_rate
step_failure_rate
approval_conversion_rate
average_completion_time
tool_failure_rate
notification_open_rate
watcher_precision
memory_hit_rate
```

---

# 25. First implementation sprints

## Sprint 1

Build:

* repo skeleton
* Postgres migrations
* core enums and Pydantic models
* tasks/runs/steps tables
* simple FastAPI app
* create/list task endpoints

## Sprint 2

Build:

* event bus abstraction
* task engine repositories
* run creation
* step persistence
* basic orchestration loop

## Sprint 3

Build:

* planner contract
* fake planner implementation
* executor contract
* dummy tools
* run trace endpoint

## Sprint 4

Build:

* approval service
* approval endpoints
* awaiting_approval run state
* resume flow

## Sprint 5

Build:

* context builder
* memory item storage
* entity storage
* simple retrieval ranking

## Sprint 6

Build:

* UI schema DSL
* Next.js shell
* task detail page
* run trace page
* approval center

## Sprint 7

Build:

* Gmail connector
* Calendar connector
* normalized models
* today briefing workflow
* meeting prep workflow

## Sprint 8

Build:

* watcher engine
* notifications
* inbox triage workflow
* proactive alerts

## Sprint 9

Build:

* browser service
* browser tools
* screenshot logs
* browser research workflow

## Sprint 10

Build:

* observability dashboard
* eval harness
* production hardening

---

# 26. Recommended first flagship workflows

Implement these first because they force most of the core architecture.

## Today briefing

Forces:

* calendar connector
* email connector
* priorities
* UI view generation
* summarization

## Meeting prep

Forces:

* context retrieval
* entity graph
* artifact synthesis
* report view

## Inbox triage

Forces:

* draft creation
* approval flow
* prioritization
* notifications

## Watch investor replies

Forces:

* watcher engine
* event routing
* proactive notification
* task-from-event creation

## Browser research report

Forces:

* browser subsystem
* artifact pipeline
* evidence-based output
* report UI

---

# 27. What to hardcode initially vs generalize later

## Hardcode initially

* task type playbooks
* UI view types
* basic policy rules
* retrieval ranking weights
* first connector mappings
* watcher templates

## Generalize later

* agent specialization
* visual UI composition logic
* dynamic policy admin
* complex DAG execution
* multi-user assignment flows
* team-level workflows

---

# 28. Exact files I’d create first

```text
apps/api/src/main.py
apps/api/src/routers/tasks.py
apps/api/src/routers/runs.py
apps/api/src/routers/approvals.py

apps/runtime/src/orchestrator/run_orchestrator.py
apps/runtime/src/planners/base.py
apps/runtime/src/planners/playbook_planner.py
apps/runtime/src/executors/base.py
apps/runtime/src/verifiers/base.py

packages/core/python/jarvis_core/events/base.py
packages/core/python/jarvis_core/enums/status.py
packages/task-engine/src/domain/models.py
packages/task-engine/src/application/services.py
packages/memory/src/semantic/service.py
packages/retrieval/src/pipelines/context_builder.py
packages/tool-gateway/src/registry/registry.py
packages/ui-schema/src/dsl/models.py

apps/web/app/tasks/[taskId]/page.tsx
apps/web/app/runs/[runId]/page.tsx
apps/web/app/approvals/page.tsx
```

---

# 29. Minimal starter class layout

## `run_orchestrator.py`

```python
class RunOrchestrator:
    def __init__(
        self,
        task_repo,
        run_repo,
        step_repo,
        planner,
        executor,
        verifier,
        context_builder,
        ui_composer,
        memory_orchestrator,
        event_bus,
    ):
        self.task_repo = task_repo
        self.run_repo = run_repo
        self.step_repo = step_repo
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.context_builder = context_builder
        self.ui_composer = ui_composer
        self.memory_orchestrator = memory_orchestrator
        self.event_bus = event_bus

    async def start(self, task_id: str) -> str:
        ...
```

## `playbook_planner.py`

```python
class PlaybookPlanner:
    async def generate_plan(self, task: dict, context_pack: dict) -> ExecutionPlan:
        ...
```

## `context_builder.py`

```python
class ContextBuilder:
    async def build(self, task_id: str, run_id: str) -> ContextPack:
        ...
```

## `registry.py`

```python
class InMemoryToolRegistry(ToolRegistry):
    def __init__(self):
        self._tools = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> ToolDefinition:
        return self._tools[tool_name]
```

---

# 30. Architectural rules you should enforce

1. **No business logic inside prompts**
2. **No direct connector calls from planner**
3. **All side effects go through tool gateway**
4. **All risky side effects pass policy engine**
5. **Every state change emits an event**
6. **Every run is resumable**
7. **All UI is schema-driven**
8. **Memory writes are filtered, not raw dumps**
9. **All browser actions are logged**
10. **Every workflow must be inspectable in trace view**

---

# 31. Best starting stack for your case

* Backend: Python + FastAPI
* ORM/migrations: SQLAlchemy + Alembic
* Workers: Arq or Celery initially
* DB: Postgres
* Queue/cache: Redis
* Frontend: Next.js + TypeScript
* Auth: your own session layer initially, external provider later
* Browser: Playwright
* Object store: S3-compatible
* Logs/search: OpenSearch later, plain structured logs first
* Models: router abstraction, not framework lock-in

---

# 32. Final recommendation on how to start coding tomorrow

Start in exactly this order:

1. Create monorepo skeleton
2. Add Postgres migrations for `users`, `workspaces`, `tasks`, `task_runs`, `task_steps`, `events`
3. Implement create/list/get task APIs
4. Implement run orchestrator with a fake planner and fake executor
5. Add run trace endpoint and simple web trace page
6. Add approval model and pause/resume flow
7. Add context pack builder and memory tables
8. Add Gmail + Calendar connectors
9. Ship today briefing + meeting prep
10. Add watcher engine

That path gets you to a real Jarvis kernel fastest.

If you want, the next best step is for me to turn this into a **code-level starter pack** with actual FastAPI models, Alembic migrations, folder contents, and scaffolding code for the first 20 files.
