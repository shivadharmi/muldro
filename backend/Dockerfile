# Jarvis backend — FastAPI API + in-process background worker.
#
# The project is run from source (it has no build-system / is not packaged),
# so we install only the third-party dependencies declared in pyproject.toml
# and launch `python run.py` from the source directory, exactly as it runs
# locally. Migrations run on container start (see docker-entrypoint.sh).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app/backend

# Install runtime dependencies only ([project].dependencies — no dev extras,
# no attempt to build the project itself). Done before copying source so the
# dependency layer is cached across code changes.
COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

# Application source.
COPY . ./
RUN chmod +x docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    JARVIS_HOST=0.0.0.0 \
    JARVIS_PORT=8000

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
