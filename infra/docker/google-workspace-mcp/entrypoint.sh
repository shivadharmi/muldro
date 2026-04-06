#!/bin/sh
# Map JARVIS_-prefixed env vars to what workspace-mcp expects.
# Docker Compose env_file loads backend/.env which uses the JARVIS_ prefix.
export GOOGLE_OAUTH_CLIENT_ID="${JARVIS_GOOGLE_OAUTH_CLIENT_ID:-$GOOGLE_OAUTH_CLIENT_ID}"
export GOOGLE_OAUTH_CLIENT_SECRET="${JARVIS_GOOGLE_OAUTH_CLIENT_SECRET:-$GOOGLE_OAUTH_CLIENT_SECRET}"

exec "$@"
