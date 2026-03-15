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
