#!/bin/bash
set -euo pipefail

# Post-deploy update script
# Usage: sudo /opt/jarvis/infra/scripts/deploy.sh [branch]

INSTALL_DIR="/opt/jarvis"
BRANCH="${1:-main}"

echo "=== Deploying Jarvis (branch: $BRANCH) ==="

cd "$INSTALL_DIR"

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

# Restart services
echo "Restarting services..."
systemctl restart jarvis-backend

# Wait and check
sleep 3
echo ""
echo "Service status:"
systemctl is-active jarvis-backend && echo "  jarvis-backend: running" || echo "  jarvis-backend: FAILED"
systemctl is-active caddy && echo "  caddy: running" || echo "  caddy: FAILED"

echo ""
echo "=== Deploy complete ==="
