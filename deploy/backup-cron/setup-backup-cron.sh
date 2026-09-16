#!/usr/bin/env bash
# One-command install of the ClutchD nightly DB backup cron.
# Usage: ./setup-backup-cron.sh          (install/refresh)
#        ./setup-backup-cron.sh --test   (also run one backup immediately)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/bin/clutchd-backup.sh"

mkdir -p "$HOME/.local/bin"
install -m 700 "$SCRIPT_DIR/clutchd-backup.sh" "$DEST"

# 03:30 IST daily. Server clock runs UTC → 22:00 UTC = 03:30 IST (UTC+5:30).
CRON_LINE="30 22 * * * $DEST >> $HOME/backups/cron-run.log 2>&1"

# `|| true`: empty crontab makes grep exit 1, which would abort under set -e.
( crontab -l 2>/dev/null | grep -v "clutchd-backup.sh" || true ; echo "$CRON_LINE" ) | crontab -

echo "installed: $DEST"
echo "cron:      $CRON_LINE"
crontab -l | grep clutchd-backup

if [[ "${1:-}" == "--test" ]]; then
  echo "--- running one backup now ---"
  "$DEST"
  tail -3 "$HOME/backups/backup.log"
fi
