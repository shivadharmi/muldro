---
description: Create a new backend service with model, endpoint, and test
user-invocable: true
---

# Add a new Muldro backend service

## Steps

1. **Ask the user**: What does the service do? What data does it manage? Which agent uses it?
2. **Read CLAUDE.md** for architecture rules and agent boundaries
3. **Create the service** at `backend/src/services/{name}.py`:
   - Async class with `__init__(self, settings, db=None)` pattern (match existing services)
   - All methods async. Use SQLAlchemy async sessions for DB.
   - Accept structured inputs, return structured data (Pydantic or dicts)
   - Use ULID string IDs with type prefix for new records
4. **Create/update models** if new tables needed at `backend/src/models/{name}.py`:
   - Inherit from `Base` (from `src.models.base`)
   - Use `TimestampMixin` for created_at/updated_at
   - String IDs with type prefix, JSONB for flexible columns
   - Add indexes for every query pattern
   - Update `backend/src/models/__init__.py` imports
5. **Register the service** in `backend/run.py:_build_services()`:
   - Follow the existing try/except pattern
   - Pass `settings` and `db_factory()` session as needed
   - Add alias if the service will be used by context assembler
6. **Create API schemas** in `backend/src/api/schemas.py`:
   - Pydantic request/response models. Never return bare dicts from endpoints.
7. **Create API route** at `backend/src/api/routes_{name}.py`:
   - Use `APIRouter(prefix="/v1/{name}", tags=["{name}"])` pattern
   - Wire into `backend/src/api/app.py` with `app.include_router()`
8. **If the service is used by agents**, register as MCP tool in `backend/src/tools/intelligence_server.py` and add to `AGENT_TOOL_SCOPES` in `backend/src/orchestrator/agents.py`
9. **Create migration** if new tables: `alembic revision --autogenerate -m "add {name}"`
10. **Write tests** at `backend/tests/test_{name}.py` using `make_mock_settings()` from conftest
11. **Run**: `cd backend && ruff check src/ tests/ && pytest tests/ -v`
