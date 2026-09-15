#!/usr/bin/env bash
# install.sh — deploy the ClutchD funnel watchdog to the server.
#
# Usage (from the workstation, repo root of ClutchD-Backend):
#   ./deploy/funnel-watchdog/install.sh            # install + enable
#   ./deploy/funnel-watchdog/install.sh --test     # install, then run one FORCE_FAIL heal cycle (safe)
#
# Requires: sshpass with the server password, or an existing SSH alias `clutchd`.
set -euo pipefail

SERVER="${SERVER:-clutchd}"
PASS="${PASS:-1907}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

run() { sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$SERVER" "$@"; }
put() { sshpass -p "$PASS" scp -o StrictHostKeyChecking=no "$1" "$SERVER:$2"; }

echo "==> Copying files"
put "$SRC_DIR/clutchd-funnel-watchdog.sh" /tmp/clutchd-funnel-watchdog.sh
put "$SRC_DIR/clutchd-funnel-watchdog.service" /tmp/clutchd-funnel-watchdog.service
put "$SRC_DIR/clutchd-funnel-watchdog.timer" /tmp/clutchd-funnel-watchdog.timer

echo "==> Installing units"
run 'set -e
echo 1907 | sudo -S install -m 755 /tmp/clutchd-funnel-watchdog.sh /usr/local/sbin/clutchd-funnel-watchdog.sh
echo 1907 | sudo -S install -m 644 /tmp/clutchd-funnel-watchdog.service /etc/systemd/system/clutchd-funnel-watchdog.service
echo 1907 | sudo -S install -m 644 /tmp/clutchd-funnel-watchdog.timer /etc/systemd/system/clutchd-funnel-watchdog.timer
rm -f /tmp/clutchd-funnel-watchdog.sh /tmp/clutchd-funnel-watchdog.service /tmp/clutchd-funnel-watchdog.timer
echo 1907 | sudo -S mkdir -p /var/lib/clutchd-funnel-watchdog
echo 1907 | sudo -S systemctl daemon-reload
echo 1907 | sudo -S systemctl enable --now clutchd-funnel-watchdog.timer
echo 1907 | sudo -S systemctl start clutchd-funnel-watchdog.service
'

echo "==> Timer + first-run status"
run 'systemctl is-active clutchd-funnel-watchdog.timer
echo 1907 | sudo -S cat /var/lib/clutchd-funnel-watchdog/last-status 2>/dev/null | tail -5'

if [ "${1:-}" = "--test" ]; then
    echo "==> TEST: forcing one stale-edge heal cycle (FORCE_FAIL=1, detached)"
    # The heal path restarts tailscaled, which kills this SSH session itself —
    # so run the watchdog detached (nohup) and poll for results afterwards.
    run 'echo 1907 | sudo -S bash -c "nohup env FORCE_FAIL=1 /usr/local/sbin/clutchd-funnel-watchdog.sh >/tmp/funnelwd-test.log 2>&1 &"; echo "launched, waiting for heal cycle (restart + 15s settle + probe)..."'
    sleep 45
    run 'echo "--- watchdog output ---"; cat /tmp/funnelwd-test.log; echo "--- status file ---"; echo 1907 | sudo -S tail -12 /var/lib/clutchd-funnel-watchdog/last-status; echo "--- tailscaled uptime ---"; systemctl show tailscaled -p ActiveEnterTimestamp'
    echo "==> Post-test local health"
    run 'curl -s -m 10 -o /dev/null -w "local: %{http_code}\n" http://127.0.0.1:8000/health'
    echo "==> Post-test EXTERNAL check (from this machine)"
    for i in 1 2 3; do
        curl -s -m 25 -o /dev/null -w "try$i: %{http_code} tls:%{time_appconnect}s total:%{time_total}s\n" \
            https://clutchd-1.tail14cfb9.ts.net/health || true
    done
fi

echo "==> Done."
