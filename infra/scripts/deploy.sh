#!/bin/bash
set -euo pipefail

# Post-deploy update script
# Usage: sudo /opt/jarvis/infra/scripts/deploy.sh [branch]

INSTALL_DIR="/opt/jarvis"
BRANCH="${1:-main}"
GATEWAY_DIR="$INSTALL_DIR/infra/gateway"
ENV_FILE="$INSTALL_DIR/backend/.env"

echo "=== Deploying Jarvis (branch: $BRANCH) ==="

cd "$INSTALL_DIR"

# ============================================================
# Preflight: the gateway is not optional
# ============================================================
# seed_installations declares google-workspace and github with
# auth_provider="platform_jwt" on EVERY backend start. _installation_to_config()
# raises GatewayNotConfigured for those unless settings.toolhive_vmcp_url is set,
# and there is deliberately no native fallback — so a host without gateway config
# silently loses Google and GitHub entirely. Fail here, before touching a running
# deployment, rather than restarting into that state.
#
# Set these in backend/.env (see infra/gateway/README.md §2 and RUNBOOK-gateway.md).
GATEWAY_REQUIRED_VARS=(
  JARVIS_TOOLHIVE_VMCP_URL              # backend -> adapter; the ONLY gateway routing switch
  JARVIS_OPENCONNECTOR_ADMIN_URL        # backend -> OC admin plane (connect/confirm flow)
  JARVIS_OPENCONNECTOR_ADMIN_TOKEN      # must equal the container's OOMOL_CONNECT_ADMIN_TOKEN
  JARVIS_PLATFORM_JWT_PRIVATE_PEM       # backend MINTS platform JWTs with this
  OOMOL_CONNECT_ENCRYPTION_KEY          # OC credential store; losing it orphans every connection
  OOMOL_CONNECT_RUNTIME_TOKEN           # gates OC POST /mcp
  OOMOL_CONNECT_ADMIN_TOKEN             # gates OC /api/* — unset means an OPEN admin plane
  JARVIS_PLATFORM_JWT_PUBLIC_PEM        # adapter VERIFIES with this (never the private key)
)

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found — cannot resolve gateway configuration." >&2
  exit 1
fi

# Presence check by grep, NOT by sourcing: sourcing .env would execute whatever is
# in it, and JARVIS_PLATFORM_JWT_PRIVATE_PEM is a multi-line value whose parse
# depends on exact quoting. Docker Compose reads the same file via --env-file below,
# so nothing here needs the values themselves — only whether each key has one.
env_has() {
  grep -Eq "^[[:space:]]*(export[[:space:]]+)?$1=[\"']?[^\"'[:space:]]" "$ENV_FILE"
}

missing=()
for var in "${GATEWAY_REQUIRED_VARS[@]}"; do
  env_has "$var" || missing+=("$var")
done
if [ ${#missing[@]} -ne 0 ]; then
  echo "ERROR: gateway configuration incomplete — refusing to deploy." >&2
  printf '  missing from %s: %s\n' "$ENV_FILE" "${missing[*]}" >&2
  echo "  Google Workspace and GitHub route ONLY through the gateway (auth_provider=" >&2
  echo "  platform_jwt) and have no native fallback, so deploying without it would" >&2
  echo "  disable both. See infra/gateway/README.md §2 and RUNBOOK-gateway.md." >&2
  exit 1
fi

# Pull latest code
sudo -u ubuntu git fetch origin
sudo -u ubuntu git checkout "$BRANCH"
sudo -u ubuntu git pull origin "$BRANCH"

# Update backend
echo "Updating backend..."
cd "$INSTALL_DIR/backend"
sudo -u ubuntu bash -c "
  cd $INSTALL_DIR/backend
  export PATH=\"/home/ubuntu/.local/bin:\$PATH\"
  source .venv/bin/activate
  [ -f .env ] && set -a && source .env && set +a
  uv pip install -e '.[dev]'
  alembic upgrade head
"

# Pre-warm MCP package caches so the first real tool call isn't a cold
# download. Best-effort and time-bounded: never fail or hang the deploy.
# Versions MUST stay in sync with backend/src/integrations/local_servers.py
# (WORKSPACE_MCP_PACKAGE) and backend/src/integrations/seed_installations.py
# (npx args) — update here when bumping those.
sudo -u ubuntu bash -c '
  export PATH="/home/ubuntu/.local/bin:$PATH"
  timeout 120 uvx workspace-mcp==1.21.3 --help >/dev/null 2>&1 || true
  timeout 120 npx -y slack-mcp-server@1.3.0 --help >/dev/null 2>&1 || true
  timeout 120 npx -y @playwright/mcp@0.0.76 --help >/dev/null 2>&1 || true
  timeout 120 npx -y @modelcontextprotocol/server-filesystem@2026.1.14 --help >/dev/null 2>&1 || true
  timeout 120 npx -y @notionhq/notion-mcp-server@2.4.0 --help >/dev/null 2>&1 || true
' || true

# ============================================================
# Gateway stack: OpenConnector + Connection Context Adapter
# ============================================================
# Brought up BEFORE the backend restarts so the adapter is already serving
# :8100/mcp when WorkspaceMCPPool.initialize_from_db registers the
# platform_jwt installations. `up -d --build` rebuilds the adapter image from
# the source we just pulled — it runs backend/Dockerfile, so a stale image
# would silently serve the previous commit's action registry.
echo "Bringing up gateway stack (openconnector + connection-adapter)..."
cd "$GATEWAY_DIR"
# --env-file: the gateway secrets live alongside the backend's in backend/.env, and
# compose's own parser handles the multi-line PEM correctly.
docker compose --env-file "$ENV_FILE" up -d --build --wait || {
  echo "ERROR: gateway stack failed to start — Google Workspace and GitHub would be" >&2
  echo "  unavailable. Not restarting the backend. Inspect with:" >&2
  echo "    cd $GATEWAY_DIR && docker compose --env-file $ENV_FILE ps && docker compose --env-file $ENV_FILE logs" >&2
  exit 1
}

# Restart services
echo "Restarting services..."
systemctl restart jarvis-backend

# Wait and check
sleep 3
echo ""
echo "Service status:"
systemctl is-active jarvis-backend && echo "  jarvis-backend: running" || echo "  jarvis-backend: FAILED"
systemctl is-active caddy && echo "  caddy: running" || echo "  caddy: FAILED"
(cd "$GATEWAY_DIR" && docker compose --env-file "$ENV_FILE" ps --status running \
  --format '  gateway: {{.Service}} running')

echo ""
echo "=== Deploy complete ==="
