#!/usr/bin/env bash
# install.sh — deploy the ClutchD API alert watchdog to the server.
#
# Usage (from the workstation, anywhere in ClutchD-Backend):
#   ./deploy/api-watchdog/install.sh            # install + enable
#   ./deploy/api-watchdog/install.sh --test     # install, then run one FORCED-failure cycle to verify email delivery
#
# Requires: sshpass with the server password, or an existing SSH alias `clutchd`.
set -euo pipefail

SERVER="${SERVER:-clutchd}"
PASS="${PASS:-1907}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

run() { sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$SERVER" "$@"; }
put() { sshpass -p "$PASS" scp -o StrictHostKeyChecking=no "$1" "$SERVER:$2"; }

echo "==> Copying files"
put "$SRC_DIR/clutchd-api-alerts.sh" /tmp/clutchd-api-alerts.sh
put "$SRC_DIR/clutchd-api-alerts.service" /tmp/clutchd-api-alerts.service
put "$SRC_DIR/clutchd-api-alerts.timer" /tmp/clutchd-api-alerts.timer

echo "==> Installing units"
run 'set -e
echo 1907 | sudo -S install -m 755 /tmp/clutchd-api-alerts.sh /usr/local/sbin/clutchd-api-alerts.sh
echo 1907 | sudo -S install -m 644 /tmp/clutchd-api-alerts.service /etc/systemd/system/clutchd-api-alerts.service
echo 1907 | sudo -S install -m 644 /tmp/clutchd-api-alerts.timer /etc/systemd/system/clutchd-api-alerts.timer
rm -f /tmp/clutchd-api-alerts.sh /tmp/clutchd-api-alerts.service /tmp/clutchd-api-alerts.timer
echo 1907 | sudo -S mkdir -p /var/lib/clutchd-api-alerts
echo 1907 | sudo -S bash -c "echo ok > /var/lib/clutchd-api-alerts/state"
echo 1907 | sudo -S systemctl daemon-reload
echo 1907 | sudo -S systemctl enable --now clutchd-api-alerts.timer
echo 1907 | sudo -S systemctl start clutchd-api-alerts.service
'

echo "==> Timer + first-run status"
run 'systemctl is-active clutchd-api-alerts.timer
echo 1907 | sudo -S tail -3 /var/log/clutchd-api-alerts.log 2>/dev/null || true'

if [ "${1:-}" = "--test" ]; then
    echo "==> TEST: forcing one failure cycle (writes failing state + fake downtime file), detached"
    run 'echo 1907 | sudo -S nohup bash -c "
      echo failing > /var/lib/clutchd-api-alerts/state
      echo 6 > /var/lib/clutchd-api-alerts/fail_count
      /usr/local/sbin/clutchd-api-alerts.sh
      echo ok > /var/lib/clutchd-api-alerts/state
      echo 0 > /var/lib/clutchd-api-alerts/fail_count
    " >/dev/null 2>&1 &'
    echo "==> Check dharaneesh8a@gmail.com — a still-down re-alert (36min wording) is the expected test email."
    echo "==> Then a recovery notice arrives on the next healthy run (timer, every 2 min)."
fi
