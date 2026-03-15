---
description: Add a new MCP tool with backend endpoint
user-invocable: true
---

# Add a new MCP tool

The user wants to add a new tool that can be called via MCP or the REST API.

1. **Read CLAUDE.md** for architecture rules
2. **Ask the user** what the tool does and what parameters it needs
3. **Create the backend endpoint**:
   - Add Pydantic schemas to `backend/src/api/schemas.py`
   - Add route handler to appropriate `backend/src/api/routes_*.py`
   - Wire into `backend/src/api/app.py` if new router
4. **Register as MCP tool** if needed in the backend's MCP server
5. **Test backend endpoint**: `cd backend && pytest tests/ -v`
