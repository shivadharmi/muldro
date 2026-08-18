#!/bin/bash
set -euo pipefail

# Daily PostgreSQL backup with 7-day retention
# Runs via cron: 0 3 * * *

BACKUP_DIR="/opt/muldro/backups"
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/muldro_$TIMESTAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "$(date -u) - Starting Postgres backup..."

# Dump via docker, compress
docker compose -f /opt/muldro/docker-compose.prod.yml exec -T postgres \
  pg_dump -U muldro -d muldro --no-owner --no-privileges \
  | gzip > "$BACKUP_FILE"

# Verify backup is non-empty
if [ ! -s "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file is empty!"
  rm -f "$BACKUP_FILE"
  exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup created: $BACKUP_FILE ($BACKUP_SIZE)"

# Remove backups older than retention period
DELETED=$(find "$BACKUP_DIR" -name "muldro_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
echo "Deleted $DELETED old backup(s)"

echo "$(date -u) - Backup complete"
