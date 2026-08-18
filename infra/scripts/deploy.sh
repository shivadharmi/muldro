#!/bin/bash
set -euo pipefail

# Post-deploy update script
# Usage: sudo /opt/muldro/infra/scripts/deploy.sh [branch]

INSTALL_DIR="/opt/muldro"
BRANCH="${1:-main}"
GATEWAY_DIR="$INSTALL_DIR/infra/gateway"
ENV_FILE="$INSTALL_DIR/backend/.env"

echo "=== Deploying Muldro (branch: $BRANCH) ==="

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
  MULDRO_TOOLHIVE_VMCP_URL              # backend -> adapter; the ONLY gateway routing switch
  MULDRO_OPENCONNECTOR_ADMIN_URL        # backend -> OC admin plane (connect/confirm flow)
  MULDRO_OPENCONNECTOR_ADMIN_TOKEN      # must equal the container's OOMOL_CONNECT_ADMIN_TOKEN
  MULDRO_PLATFORM_JWT_PRIVATE_PEM       # backend MINTS platform JWTs with this
  OOMOL_CONNECT_ENCRYPTION_KEY          # OC credential store; losing it orphans every connection
  OOMOL_CONNECT_RUNTIME_TOKEN           # gates OC POST /mcp
  OOMOL_CONNECT_ADMIN_TOKEN             # gates OC /api/* — unset means an OPEN admin plane
  MULDRO_PLATFORM_JWT_PUBLIC_PEM        # adapter VERIFIES with this (never the private key)
  MULDRO_DATABASE_URL                   # backend's own DB; also compose's fallback for the adapter
  MULDRO_GATEWAY_DATABASE_URL           # adapter's DB URL as seen from INSIDE its container
)
# Why MULDRO_GATEWAY_DATABASE_URL is required here and not just in local dev:
# infra/user-data.sh writes MULDRO_DATABASE_URL=...@127.0.0.1:5432/... because the
# backend runs as a host process. The adapter runs in a container, where 127.0.0.1
# is the container itself — that URL resolves to nothing and every connection
# lookup fails lazily, long after `--wait` has reported the stack healthy. On this
# host the two URLs can never be the same value, so demand both.

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found — cannot resolve gateway configuration." >&2
  exit 1
fi

# Presence check by parsing, NOT by sourcing: sourcing .env would execute whatever is
# in it, and MULDRO_PLATFORM_JWT_PRIVATE_PEM is a multi-line value whose parse
# depends on exact quoting. Docker Compose reads the same file via --env-file below,
# so nothing here needs the values themselves — only whether each key has one.
#
# Scan line by line and inspect the extracted value rather than matching one
# regex against the whole assignment. The regex form required a non-space
# character immediately after the optional opening quote, which rejected the
# canonical multi-line PEM whose opening quote ends the line:
#     MULDRO_PLATFORM_JWT_PUBLIC_PEM="
#     -----BEGIN PUBLIC KEY-----
#     ...
#     "
# i.e. it failed exactly the value this comment block exists to accommodate.
#
# Present  = the key is assigned something other than nothing, whitespace, or an
#            empty quote pair. A bare opening quote counts: it opens a
#            multi-line value.
# Missing  = `KEY=`, `KEY=   `, `KEY=""`, `KEY=''`, a commented-out line, or no
#            such key at all.
# Later assignments override earlier ones, matching how both the shell and
# Compose read a duplicated key.
env_has() {
  local key="$1"
  local line value found=1

  # `|| [ -n "$line" ]` so a final line with no trailing newline is still read.
  while IFS= read -r line || [ -n "$line" ]; do
    # Strip leading whitespace, then an `export ` prefix, then whitespace again.
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    line="${line#"${line%%[![:space:]]*}"}"

    # Only a real assignment for this key counts; `#KEY=v` stays a comment
    # because the `#` is still there after the strips above.
    case "$line" in
      "$key"=*) ;;
      *) continue ;;
    esac

    value="${line#*=}"
    # Trailing whitespace is not a value: `KEY=   ` is as empty as `KEY=`.
    value="${value%"${value##*[![:space:]]}"}"

    case "$value" in
      ''|'""'|"''") found=1 ;;   # assigned, but assigned nothing
      *) found=0 ;;
    esac
  done < "$ENV_FILE"

  return "$found"
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
systemctl restart muldro-backend

# Wait and check
sleep 3
echo ""
echo "Service status:"
systemctl is-active muldro-backend && echo "  muldro-backend: running" || echo "  muldro-backend: FAILED"
systemctl is-active caddy && echo "  caddy: running" || echo "  caddy: FAILED"
# `|| echo`, like every status line above it: this is a report, not a gate. The
# gateway was already gated by `up --wait` failing hard earlier, so a non-zero
# exit from `ps` here (compose hiccup, format error) must not make an otherwise
# successful deploy exit non-zero under `set -e` before printing its result.
(cd "$GATEWAY_DIR" && docker compose --env-file "$ENV_FILE" ps --status running \
  --format '  gateway: {{.Service}} running') || echo "  gateway: status unavailable"

echo ""
echo "=== Deploy complete ==="
