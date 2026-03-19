---
description: Create and apply database migrations with Alembic
user-invocable: true
---

# Database migration

1. Read the model changes in `backend/src/models/` that need migration
2. Ensure Docker Compose is running: `docker compose up -d`
3. From `backend/`:
   ```bash
   cd backend && source .venv/bin/activate
   alembic revision --autogenerate -m "description of changes"
   ```
4. Review the generated file in `backend/alembic/versions/` — check for:
   - Correct table names (snake_case)
   - String IDs with type prefix (e.g., `evt_`, `plan_`, `mem_`)
   - JSONB for flexible columns, typed columns for indexed fields
   - Indexes for every query pattern used in services
   - `TimestampMixin` columns (created_at, updated_at) if using the mixin
5. Apply: `alembic upgrade head`
6. Verify: `alembic current`

**Rules:**
- Never modify existing applied migrations — create new ones
- Use ULID-based string IDs, never auto-increment integers
- Add `__init__.py` model imports if you created a new model file
- If data migration needed, write explicit `op.execute()` SQL in the migration
