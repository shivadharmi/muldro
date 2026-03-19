---
description: Add a new API route with schemas, handler, and test
user-invocable: true
---

# Add a new API route

All API routes live in `backend/src/api/routes_{name}.py` and are `/v1/` prefixed.

## Steps

1. **Ask the user**: What resource does this route manage? What HTTP methods are needed?
2. **Read existing route files** for patterns (e.g., `routes_tasks.py`, `routes_goals.py`)
3. **Add Pydantic schemas** in `backend/src/api/schemas.py`:
   - Request models for POST/PUT bodies
   - Response models for all returns (never bare dicts)
4. **Create the route file** at `backend/src/api/routes_{name}.py`:
   ```python
   from fastapi import APIRouter, Depends, HTTPException
   router = APIRouter(prefix="/v1/{name}", tags=["{name}"])
   ```
   - All handlers async
   - Use `Depends()` for auth and service injection
   - Return Pydantic response models
   - Use proper HTTP methods (GET list, GET by ID, POST create, PUT update, DELETE)
   - Use HTTPException for errors (404, 400, 409, etc.)
5. **Wire into app** in `backend/src/api/app.py`:
   ```python
   from src.api.routes_{name} import router as {name}_router
   app.include_router({name}_router)
   ```
6. **Write route tests** at `backend/tests/test_{name}_routes.py` using FastAPI TestClient
7. **Run**: `cd backend && ruff check src/ tests/ && pytest tests/ -v`
