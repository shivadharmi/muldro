---
description: Add a new OpenClaw agent tool with backend endpoint
user-invocable: true
---

# Add a new OpenClaw agent tool

The user wants to add a new tool that the Claude model can call through OpenClaw.

1. **Read CLAUDE.md** for plugin rules (thin bridge, no business logic)
2. **Ask the user** what the tool does and what parameters it needs
3. **Create the backend endpoint first** — the tool is just an HTTP wrapper:
   - Add Pydantic schemas to `backend/src/api/schemas.py`
   - Add route handler to appropriate `backend/src/api/routes_*.py`
   - Wire into `backend/src/api/app.py` if new router
4. **Add the tool** in `jarvis-tools/src/tools.ts`:
   - Use TypeBox for parameter schema
   - Tool execute() calls backend via `callBackend(config, path, method, body)`
   - Return format: `{ content: [{ type: "text", text: "..." }] }`
   - Use `{ optional: true }` for non-essential tools
5. **Add to OpenClaw config** — add tool name to `tools.allow` in `openclaw.example.json5`
6. **Update SOUL.md** — add the tool to the agent's capability list
7. **Type-check**: `cd jarvis-tools && npx tsc --noEmit`
8. **Test backend endpoint**: `cd backend && pytest tests/ -v`
