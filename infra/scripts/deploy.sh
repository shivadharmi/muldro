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
# download. Best-effort: never fail the deploy if a registry is unreachable.
sudo -u ubuntu bash -c '
  export PATH="/home/ubuntu/.local/bin:$PATH"
  uvx workspace-mcp --help >/dev/null 2>&1 || true
  npx -y slack-mcp-server --help >/dev/null 2>&1 || true
  npx -y @playwright/mcp --help >/dev/null 2>&1 || true
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
