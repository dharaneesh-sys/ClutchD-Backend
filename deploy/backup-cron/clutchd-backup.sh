#!/usr/bin/env bash
# ClutchD nightly database backup — pg_dump | gzip, 7-day retention, logged.
# Runs via crontab at 03:30 IST daily (see deploy/backup-cron/setup-backup-cron.sh).

set -euo pipefail

BACKUP_DIR="$HOME/backups"
DB_NAME="clutchd"
DB_USER="clutchd"
DB_HOST="127.0.0.1"
RETAIN_DAYS=7
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M)"
FILE="$BACKUP_DIR/${DB_NAME}_db_${STAMP}.sql.gz"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

export PGPASSWORD="clutchd"

if pg_dump -h "$DB_HOST" -U "$DB_USER" "$DB_NAME" | gzip > "$FILE.tmp"; then
  mv "$FILE.tmp" "$FILE"
  SIZE=$(du -h "$FILE" | cut -f1)
  log "OK  $FILE ($SIZE)"
else
  rm -f "$FILE.tmp"
  log "FAIL pg_dump exited non-zero — backup aborted"
  exit 1
fi

# Retention: delete backups older than RETAIN_DAYS
find "$BACKUP_DIR" -name "${DB_NAME}_db_*.sql.gz" -mtime "+$((RETAIN_DAYS - 1))" -delete
# Legacy uncompressed dumps from manual runs — keep 7 days of those too
find "$BACKUP_DIR" -name "${DB_NAME}_db_*.sql" -mtime "+$((RETAIN_DAYS - 1))" -delete

log "retention sweep done (keep ${RETAIN_DAYS}d)"
