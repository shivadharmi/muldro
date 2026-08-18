#!/bin/sh
# Apply database migrations, then start the API together with the background
# worker (StreamConsumer + Scheduler) in a single container. Postgres readiness
# is guaranteed by the compose `depends_on: condition: service_healthy` gate.
set -e

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Starting Muldro API + worker..."
exec python run.py --worker
