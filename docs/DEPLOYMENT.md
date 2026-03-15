# Jarvis AWS Deployment Guide

Step-by-step runbook for deploying Jarvis to AWS, based on the actual production deployment.

## Prerequisites

- AWS account with admin access
- Domain managed in Route53 (e.g., `brrdcast.in`)
- Terraform >= 1.5 installed locally
- An EC2 key pair created in your target region
- API keys: Anthropic (or Bedrock access), Voyage AI
- GitHub Personal Access Token (for private repo cloning)

## 1. Configure Terraform Variables

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region       = "ap-south-1"
instance_type    = "t3.medium"
root_volume_size = 40

key_pair_name    = "your-key-pair-name"
allowed_ssh_cidr = "x.x.x.x/32"  # Your IP

domain_name = "brrdcast.in"
subdomain   = "jarvis"

github_repo_url = "https://github.com/your-org/jarvis.git"
git_branch      = "main"
project_name    = "jarvis"
```

Set secrets via environment variables (never commit to tfvars):

```bash
export TF_VAR_anthropic_api_key="<your-anthropic-api-key>"
export TF_VAR_voyage_api_key="<your-voyage-api-key>"
export TF_VAR_backend_token="$(openssl rand -hex 32)"
export TF_VAR_postgres_password=$(openssl rand -hex 24)
export TF_VAR_github_pat="<your-github-pat>"
```

## 2. Provision Infrastructure

```bash
cd infra
terraform init
terraform plan    # Review what will be created
terraform apply   # Type "yes" to confirm
```

This creates:
- VPC with public subnet
- EC2 instance (Ubuntu 24.04) with IAM role for SSM + Bedrock
- Route53 A record (`jarvis.brrdcast.in`)
- Security group (SSH + HTTP + HTTPS)
- SSM Parameter Store entries for all secrets

## 3. What Happens Automatically (user-data.sh)

The EC2 instance runs a 9-phase bootstrap script on first boot:

1. **System packages** — Docker, Node.js 22, Python 3, Caddy, uv, AWS CLI
2. **Security hardening** — SSH hardening, fail2ban, UFW firewall, kernel sysctl
3. **Retrieve secrets** — Pulls all secrets from SSM Parameter Store
4. **Clone repository** — Clones the private repo using GitHub PAT
5. **Start databases** — Docker Compose for Postgres + Redis, enables pgvector
6. **Backend setup** — Creates `.env`, installs Python deps, runs Alembic migrations, starts systemd service
7. **Caddy reverse proxy** — Configures TLS termination, routes `/v1/*` to backend
8. **Monitoring & backups** — Daily Postgres backup cron (3am, 7-day retention)

Monitor bootstrap progress:

```bash
ssh ubuntu@<instance-ip>
tail -f /var/log/jarvis-setup.log
```

## 4. Post-Deploy Verification

```bash
ssh ubuntu@<instance-ip>

# Check all services are running
systemctl status jarvis-backend caddy

# Check backend health
curl http://localhost:8000/v1/health

# Check Caddy TLS
curl https://jarvis.brrdcast.in/v1/health

# Check logs
journalctl -u jarvis-backend -f
journalctl -u caddy -f
```

## 5. Bedrock Model Access Setup

If using AWS Bedrock instead of direct Anthropic API:

1. **Enable model access** in the AWS Console:
   - Go to Amazon Bedrock > Model access > Manage model access
   - Enable Claude Sonnet 4 (or your preferred model)
   - Wait for access to be granted (usually instant)

2. **Create inference profile** (required for cross-region or regional access):
   - Bedrock model IDs use region-prefixed inference profiles
   - For `ap-south-1`: the ID is `apac.anthropic.claude-sonnet-4-20250514-v1:0`
   - For `us-east-1`: the ID is `us.anthropic.claude-sonnet-4-20250514-v1:0`

3. **IAM policy** (already configured by Terraform):
   - The EC2 instance role has `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` permissions

4. **Backend config** (in `.env`):
   ```
   JARVIS_USE_BEDROCK=true
   JARVIS_BEDROCK_REGION=ap-south-1
   JARVIS_ANTHROPIC_MODEL=apac.anthropic.claude-sonnet-4-20250514-v1:0
   ```

## 6. Telegram Bot Configuration

Set the following environment variables in `backend/.env`:

```
JARVIS_TELEGRAM_BOT_TOKEN=<bot-token-from-botfather>
JARVIS_TELEGRAM_CHAT_ID=<your-telegram-chat-id>
```

The backend's scheduler delivers briefings and notifications directly via the Telegram Bot API.

## 7. Updating Deployed Code

```bash
ssh ubuntu@<instance-ip>
sudo /opt/jarvis/infra/scripts/deploy.sh [branch]
```

This pulls latest code, installs dependencies, runs migrations, and restarts services.

## 8. Backup and Restore

**Backups** run automatically at 3am daily, stored in `/opt/jarvis/backups/` with 7-day retention.

**Manual backup:**

```bash
sudo /usr/local/bin/backup-postgres.sh
```

**Restore from backup:**

```bash
gunzip -c /opt/jarvis/backups/jarvis_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose -f /opt/jarvis/docker-compose.prod.yml exec -T postgres \
  psql -U jarvis -d jarvis
```

## Common Issues and Solutions

### `systemctl restart ssh` fails on Ubuntu 24.04

Ubuntu 24.04 uses `ssh.service` not `sshd.service`. The user-data script uses `systemctl restart ssh` which is correct. If you see `sshd` in error messages, it's a red herring — check `ssh.service` instead.

### Bedrock inference profile required

When using Bedrock, you cannot use bare model IDs like `anthropic.claude-sonnet-4-20250514-v1:0`. You must use inference profile IDs which have a region prefix:
- `ap-south-1` -> `apac.anthropic.claude-sonnet-4-20250514-v1:0`
- `us-east-1` -> `us.anthropic.claude-sonnet-4-20250514-v1:0`
- `eu-west-1` -> `eu.anthropic.claude-sonnet-4-20250514-v1:0`

### `alembic.ini` DB URL mismatch

The `alembic.ini` file has a `sqlalchemy.url` value that must match your actual Postgres password. On deploy, the user-data script patches this automatically. If you run migrations manually, ensure the URL matches your `.env`.

