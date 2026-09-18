#!/usr/bin/env bash
# install.sh — deploy the ClutchD SSH lifeline (drop-ins + 30s watchdog).
#
# Usage (from the workstation, anywhere in ClutchD-Backend):
#   ./deploy/ssh-lifeline/install.sh
#
# Idempotent: safe to re-run (drop-ins overwritten, timer re-enabled).
# Requires: sshpass with the server password, or an existing SSH alias `clutchd`.
set -euo pipefail

SERVER="${SERVER:-clutchd}"
PASS="${PASS:-1907}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

run() { sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$SERVER" "$@"; }
put() { sshpass -p "$PASS" scp -o StrictHostKeyChecking=no "$1" "$SERVER:$2"; }

echo "==> Copying files"
put "$SRC_DIR/tailscaled-restart.conf" /tmp/tailscaled-restart.conf
put "$SRC_DIR/ssh-restart.conf" /tmp/ssh-restart.conf
put "$SRC_DIR/clutchd-ssh-lifeline.sh" /tmp/clutchd-ssh-lifeline.sh
put "$SRC_DIR/clutchd-ssh-lifeline.service" /tmp/clutchd-ssh-lifeline.service
put "$SRC_DIR/clutchd-ssh-lifeline.timer" /tmp/clutchd-ssh-lifeline.timer

echo "==> Installing drop-ins (Restart=always) + script + units"
run 'set -e
echo 1907 | sudo -S mkdir -p /etc/systemd/system/tailscaled.service.d /etc/systemd/system/ssh.service.d
echo 1907 | sudo -S install -m 644 /tmp/tailscaled-restart.conf /etc/systemd/system/tailscaled.service.d/zz-clutchd-restart.conf
echo 1907 | sudo -S install -m 644 /tmp/ssh-restart.conf /etc/systemd/system/ssh.service.d/zz-clutchd-restart.conf
echo 1907 | sudo -S install -m 755 /tmp/clutchd-ssh-lifeline.sh /usr/local/sbin/clutchd-ssh-lifeline.sh
echo 1907 | sudo -S install -m 644 /tmp/clutchd-ssh-lifeline.service /etc/systemd/system/clutchd-ssh-lifeline.service
echo 1907 | sudo -S install -m 644 /tmp/clutchd-ssh-lifeline.timer /etc/systemd/system/clutchd-ssh-lifeline.timer
rm -f /tmp/tailscaled-restart.conf /tmp/ssh-restart.conf /tmp/clutchd-ssh-lifeline.sh /tmp/clutchd-ssh-lifeline.service /tmp/clutchd-ssh-lifeline.timer
echo 1907 | sudo -S systemctl daemon-reload
echo 1907 | sudo -S systemctl restart tailscaled
echo 1907 | sudo -S systemctl restart ssh
echo 1907 | sudo -S systemctl enable --now clutchd-ssh-lifeline.timer
echo INSTALLED
echo "--- verify Restart policies ---"
systemctl show tailscaled -p Restart
systemctl show ssh -p Restart
systemctl is-active clutchd-ssh-lifeline.timer
'
echo "==> Done. tailscaled + sshd now Restart=always; lifeline watchdog runs every 30s."
