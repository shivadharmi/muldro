---
description: Add a new MCP tool to the intelligence or communication server
user-invocable: true
---

# Add a new MCP tool

Tools are how sub-agents interact with the intelligence layer and external world. There are two internal MCP servers:

- **Intelligence server** (`backend/src/tools/intelligence_server.py`): Tools for event ingestion, memory, entities, planning, policy, approvals, cursors, tasks, context, verification
- **Communication server** (`backend/src/tools/communication_server.py`): Tools for Telegram, approval prompts, A2UI surface updates

## Steps

1. **Ask the user**: What does the tool do? Which agent(s) will use it? Does it read or write?
2. **Read the target server file** to understand existing patterns
3. **Add the tool function** using the `@intelligence.tool()` or `@communication.tool()` decorator:
   - Async function with typed parameters and clear docstring
   - Access services via module-level `_services` dict (configured at startup)
   - Use `_get_db()` context manager for database sessions
   - Return a dict with structured results
4. **Register in agent tool scopes** at `backend/src/orchestrator/agents.py`:
   - Add the tool name to `AGENT_TOOL_SCOPES` for the appropriate agent(s)
   - Respect boundaries: Observer reads, Operator writes, Researcher is read-only
   - External writes must go through Operator (Governor-gated)
5. **Write a test** in `backend/tests/` that mocks services and verifies tool behavior
6. **Run**: `cd backend && ruff check src/ tests/ && pytest tests/ -v`

## Tool scope rules (from agents.py)

| Agent | Allowed tool types |
|-------|-------------------|
| Observer | External reads + ingest_event + cursors |
| Librarian | Entity/memory writes |
| Planner | Plan creation + memory/entity reads |
| Governor | Policy evaluation + approvals |
| Operator | External writes (Gmail send, Calendar create, Slack post, GitHub) |
| Presenter | Briefings + communication (Telegram, A2UI) |
| Researcher | All reads + web search + browser (no writes) |
| Persona | Memory search + preference extraction |

If the tool needs a new service dependency, also wire it into `backend/run.py:_build_services()`.
