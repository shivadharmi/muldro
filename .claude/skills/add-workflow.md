---
description: Create a new durable workflow (daily brief, meeting prep, etc.)
user-invocable: true
---

# Add a new Jarvis workflow

The user wants to add a new durable workflow (e.g., daily briefing, meeting prep, email follow-up).

1. **Read CLAUDE.md** and **docs/ARCHITECTURE.md** for the agent role boundaries
2. **Ask the user** what triggers the workflow and what it produces
3. **Design the workflow steps**:
   - What events/data does it read?
   - What entities does it update?
   - What does the planner decide?
   - Does it need approval?
   - What does the presenter output?
4. **Create the workflow** at `backend/src/workflows/{name}.py`:
   - Top-level async function as entry point
   - Clear step-by-step comments
   - Respect agent boundaries: only Planner decides intent, only Operator calls tools
   - Use correlation IDs throughout
5. **Add a trigger mechanism**:
   - If cron-triggered: document the OpenClaw cron job config
   - If event-triggered: wire from EventProcessor
   - If user-triggered: add API endpoint + OpenClaw tool
6. **Add presenter output**:
   - Define the output schema in `backend/src/api/schemas.py`
   - The Presenter turns internal state into user-facing content
7. **Write tests** for the workflow logic
8. **Run lint and tests**: `ruff check src/ && pytest tests/ -v`
