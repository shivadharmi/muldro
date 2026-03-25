---
description: Create a new durable workflow (daily brief, meeting prep, inbox triage, etc.)
user-invocable: true
---

# Add a new Jarvis workflow

Workflows are multi-step compositions in `backend/src/workflows/`. They use the `WorkflowRegistry` pattern from `backend/src/workflows/workflow_registry.py`.

## Steps

1. **Ask the user**: What triggers this workflow? What does it produce? Does any step need approval?
2. **Read existing workflows** for patterns:
   - `backend/src/workflows/daily_briefing.py` — scheduler-triggered, produces briefing
   - `backend/src/workflows/inbox_triage.py` — email classification + draft responses (approval-gated)
   - `backend/src/workflows/research_agent.py` — search + cross-reference + report
3. **Design the workflow steps**:
   - What events/data does it read? (Observer scope)
   - What entities does it update? (Librarian scope)
   - What does the Planner decide? (structured task graph, never free-form)
   - Does any step require approval? (Governor gate)
   - What does the Presenter output?
4. **Create the workflow** at `backend/src/workflows/{name}.py`:
   - Define step handler functions (async, accept context dict, return result dict)
   - Create `WorkflowStep` instances with `requires_approval` flag for external writes
   - Register a `Workflow` with the `WorkflowRegistry`
   - Use correlation IDs throughout for traceability
5. **Add a trigger**:
   - **Cron-triggered**: Add action to `backend/src/services/scheduler.py` schedule entries
   - **Event-triggered**: Wire from EventProcessor or TriggerEngine
   - **User-triggered**: User tells Jarvis in chat; Planner creates task graph via `set_instruction` decision
6. **Add presenter output**: Define output schema, use Presenter to format user-facing content
7. **Write tests** for each step handler
8. **Run**: `cd backend && ruff check src/ tests/ && pytest tests/ -v`

## Agent boundary rules for workflows
- Only Planner decides intent (structured JSON task graphs)
- Only Operator executes external actions (Gmail send, Calendar create, etc.)
- Only Presenter generates user-facing output
- Governor sits before every external write
