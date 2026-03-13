---
description: Create and apply database migrations with Alembic
user-invocable: true
---

# Database migration

1. Ensure the backend venv is active: `cd backend && source .venv/bin/activate`
2. Ensure Docker Compose is running: `docker compose up -d`
3. Review the model changes in `backend/src/models/`
4. Generate migration:
   ```bash
   cd backend && alembic revision --autogenerate -m "description of changes"
   ```
5. Review the generated migration file in `backend/alembic/versions/`
6. Apply: `alembic upgrade head`
7. Verify: `alembic current`

If the migration needs manual adjustment (e.g., data migration, custom SQL), edit the generated file before applying.

Never modify existing migrations that have been applied. Create new migrations for changes.
