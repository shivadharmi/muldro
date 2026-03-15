#!/bin/bash
set -euo pipefail

exec > >(tee /var/log/jarvis-setup.log) 2>&1
echo "=== Jarvis bootstrap started at $(date -u) ==="

DOMAIN="${domain}"
AWS_REGION="${aws_region}"
PROJECT="${project_name}"
REPO_URL="${github_repo}"
GIT_BRANCH="${git_branch}"
INSTALL_DIR="/opt/$PROJECT"

# ============================================================
# Phase 1: System packages
# ============================================================
echo "=== Phase 1: System packages ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

# Core tools
apt-get install -y \
  curl wget git jq unzip software-properties-common \
  apt-transport-https ca-certificates gnupg lsb-release \
  fail2ban ufw unattended-upgrades

# Docker (official repo)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

# Node.js 22 (NodeSource)
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -

# Caddy (official repo)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin nodejs caddy

# Python 3.12 (ships with Ubuntu 24.04)
apt-get install -y python3 python3-pip python3-venv

# uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

# AWS CLI v2
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws

# Add ubuntu user to docker group
usermod -aG docker ubuntu

# ============================================================
# Phase 2: Security hardening
# ============================================================
echo "=== Phase 2: Security hardening ==="

# SSH hardening
cat > /etc/ssh/sshd_config.d/hardening.conf <<'SSHEOF'
PermitRootLogin no
PasswordAuthentication no
MaxAuthTries 3
LoginGraceTime 30
AllowUsers ubuntu
X11Forwarding no
AllowTcpForwarding no
SSHEOF
systemctl restart ssh

# fail2ban: SSH jail
cat > /etc/fail2ban/jail.local <<'F2BEOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
bantime = 3600
findtime = 600
F2BEOF
systemctl enable fail2ban
systemctl restart fail2ban

# Unattended upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'UUEOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
UUEOF
systemctl enable unattended-upgrades

# UFW firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Kernel hardening
cat > /etc/sysctl.d/99-security.conf <<'SYSEOF'
net.ipv4.ip_forward = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
SYSEOF
sysctl --system

# ============================================================
# Phase 3: Retrieve secrets from SSM Parameter Store
# ============================================================
echo "=== Phase 3: Retrieving secrets from SSM ==="

get_ssm_param() {
  aws ssm get-parameter \
    --name "/$PROJECT/$1" \
    --with-decryption \
    --region "$AWS_REGION" \
    --query 'Parameter.Value' \
    --output text
}

ANTHROPIC_API_KEY=$(get_ssm_param "anthropic-api-key")
VOYAGE_API_KEY=$(get_ssm_param "voyage-api-key")
BACKEND_TOKEN=$(get_ssm_param "backend-token")
POSTGRES_PASSWORD=$(get_ssm_param "postgres-password")
GITHUB_PAT=$(get_ssm_param "github-pat")

echo "Secrets retrieved successfully"

# ============================================================
# Phase 4: Clone repository
# ============================================================
echo "=== Phase 4: Cloning repository ==="

# Insert PAT into HTTPS URL for private repo access
AUTH_REPO_URL=$(echo "$REPO_URL" | sed "s|https://|https://$GITHUB_PAT@|")
git clone --branch "$GIT_BRANCH" "$AUTH_REPO_URL" "$INSTALL_DIR"
unset AUTH_REPO_URL
chown -R ubuntu:ubuntu "$INSTALL_DIR"

# ============================================================
# Phase 5: Start Postgres + Redis via Docker Compose
# ============================================================
echo "=== Phase 5: Starting databases ==="

# Docker log rotation
cat > /etc/docker/daemon.json <<'DJEOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
DJEOF
systemctl restart docker

cd "$INSTALL_DIR"
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" docker compose -f docker-compose.prod.yml up -d

# Wait for Postgres to be ready
echo "Waiting for Postgres..."
for i in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml exec -T postgres pg_isready -U jarvis >/dev/null 2>&1; then
    echo "Postgres is ready"
    break
  fi
  sleep 2
done

# Enable pgvector extension
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U jarvis -d jarvis -c "CREATE EXTENSION IF NOT EXISTS vector;"

# ============================================================
# Phase 6: Set up Jarvis backend
# ============================================================
echo "=== Phase 6: Setting up Jarvis backend ==="

# Create .env file
cat > "$INSTALL_DIR/backend/.env" <<ENVEOF
JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:$POSTGRES_PASSWORD@127.0.0.1:5432/jarvis
JARVIS_REDIS_URL=redis://127.0.0.1:6379/0
JARVIS_ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
JARVIS_VOYAGE_API_KEY=$VOYAGE_API_KEY
JARVIS_BACKEND_TOKEN=$BACKEND_TOKEN
JARVIS_HOST=127.0.0.1
JARVIS_PORT=8000
JARVIS_USE_BEDROCK=true
JARVIS_BEDROCK_REGION=$AWS_REGION
JARVIS_ANTHROPIC_MODEL=apac.anthropic.claude-sonnet-4-20250514-v1:0
ENVEOF
chmod 600 "$INSTALL_DIR/backend/.env"

# Install uv for ubuntu user
sudo -u ubuntu bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

# Set up Python environment and run migrations
DB_URL="postgresql+asyncpg://jarvis:$POSTGRES_PASSWORD@127.0.0.1:5432/jarvis"
sudo -u ubuntu sed -i "s|sqlalchemy.url = .*|sqlalchemy.url = $DB_URL|" "$INSTALL_DIR/backend/alembic.ini"

sudo -u ubuntu bash -c "
  export PATH='\$HOME/.local/bin:\$PATH'
  cd $INSTALL_DIR/backend
  uv venv
  source .venv/bin/activate
  uv pip install -e '.[dev]'
  alembic upgrade head
"

# Create systemd service for backend
cat > /etc/systemd/system/jarvis-backend.service <<SVCEOF
[Unit]
Description=Jarvis Backend (FastAPI + Worker)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=$INSTALL_DIR/backend
Environment=PATH=$INSTALL_DIR/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$INSTALL_DIR/backend/.env
ExecStart=$INSTALL_DIR/backend/.venv/bin/python run.py --worker
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=jarvis-backend

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable jarvis-backend
systemctl start jarvis-backend

# ============================================================
# Phase 7: Set up Caddy reverse proxy
# ============================================================
echo "=== Phase 7: Setting up Caddy ==="

mkdir -p /var/log/caddy

cat > /etc/caddy/Caddyfile <<CADDYEOF
$DOMAIN {
    # Jarvis backend API
    handle /v1/* {
        reverse_proxy localhost:8000
    }

    # Default: Jarvis backend
    handle {
        reverse_proxy localhost:8000
    }

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    # Request size limit
    request_body {
        max_size 1MB
    }

    log {
        output file /var/log/caddy/access.log
        format json
    }
}
CADDYEOF

systemctl enable caddy
systemctl restart caddy

# ============================================================
# Phase 8: Monitoring & backups
# ============================================================
echo "=== Phase 8: Setting up backups ==="

# Install backup script
cp "$INSTALL_DIR/infra/scripts/backup-postgres.sh" /usr/local/bin/backup-postgres.sh
chmod +x /usr/local/bin/backup-postgres.sh

# Daily backup cron at 3am
echo "0 3 * * * root /usr/local/bin/backup-postgres.sh" > /etc/cron.d/jarvis-backup

echo "=== Jarvis bootstrap completed at $(date -u) ==="
echo "Services: caddy, jarvis-backend"
echo "Logs: journalctl -u <service> -f"
echo "Bootstrap log: /var/log/jarvis-setup.log"
