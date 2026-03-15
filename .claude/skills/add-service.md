---
description: Create a new backend service with model, endpoint, schema, and test
user-invocable: true
---

# Add a new Jarvis backend service

The user wants to add a new service to the Jarvis backend. Follow these steps exactly:

1. **Read the CLAUDE.md** to understand coding standards and architecture rules
2. **Read docs/CONTRACTS.md** to understand data contract patterns
3. **Ask the user** what the service does and what entities/data it manages
4. **Create the service file** at `backend/src/services/{name}.py`:
   - Async class with clear docstring explaining responsibilities
   - Methods follow the pattern: accept user_id + inputs, return structured data
   - Use SQLAlchemy async sessions for DB access
5. **Create/update models** if new tables are needed at `backend/src/models/{name}.py`:
   - Use TimestampMixin for created_at/updated_at
   - Use String IDs with type prefix
   - Add proper indexes for query patterns
   - Update `backend/src/models/__init__.py`
6. **Create API schemas** in `backend/src/api/schemas.py`:
   - Pydantic request and response models
   - Never return bare dicts
7. **Create API route** at `backend/src/api/routes_{name}.py`:
   - Use router pattern consistent with existing routes
   - Add Depends(get_current_user) for auth
   - Wire into `backend/src/api/app.py`
8. **Create a test** at `backend/tests/test_{name}.py`
9. **Run lint and tests**: `ruff check src/ tests/ && pytest tests/ -v`
10. If there's a corresponding MCP tool needed, register it in the backend's MCP server
