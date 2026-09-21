#!/usr/bin/env bash
# Set up the backup SSH path on the server: Tailscale funnel TCP 2222 -> sshd :22.
#
# Run ON THE SERVER (or via `ssh clutchd`): sudo ./setup-funnel-ssh.sh "<pubkey>"
# Idempotent: safe to run repeatedly.
#
# After this, SSH works even when the tailnet path is broken:
#   ssh -p 8443 clutchd@clutchd-1.tail14cfb9.ts.net
#
# Funnel only listens on 443/8443/10000; 443 is the API's HTTPS funnel, so the
# SSH raw-TCP forwarder goes on 8443. Raw TCP (not TLS-terminated) so the SSH
# protocol passes through untouched.
# Requirements: tailscale >= 1.38 (funnel TCP), funnel enabled for the tailnet,
# and an authorized public key (password auth stays off).

set -euo pipefail

PUBKEY="${1:-}"
if [[ -z "$PUBKEY" ]]; then
  echo "usage: sudo ./setup-funnel-ssh.sh \"<ssh-ed25519 public key>\"" >&2
  exit 1
fi

echo "── 1. authorize key for user clutchd ──"
sudo -u clutchd mkdir -p ~clutchd/.ssh
sudo -u clutchd touch ~clutchd/.ssh/authorized_keys
# add only if missing (idempotent)
if ! sudo -u clutchd grep -qxF "$PUBKEY" ~clutchd/.ssh/authorized_keys; then
  echo "$PUBKEY" | sudo -u clutchd tee -a ~clutchd/.ssh/authorized_keys > /dev/null
fi
chmod 700 ~clutchd/.ssh
chmod 600 ~clutchd/.ssh/authorized_keys

echo "── 2. ensure sshd allows pubkey auth ──"
SSHD_CONFIG="/etc/ssh/sshd_config"
if ! grep -Eq "^\s*PubkeyAuthentication\s+yes" "$SSHD_CONFIG"; then
  echo "PubkeyAuthentication yes" | sudo tee -a "$SSHD_CONFIG" > /dev/null
  sudo systemctl reload ssh || sudo systemctl reload sshd
fi

echo "── 3. enable funnel TCP 8443 -> :22 ──"
# NOTE: capture-then-grep (pipefail SIGPIPE trap — see clutchd-ssh-lifeline.sh)
FUNNEL_NOW="$(tailscale funnel status 2>/dev/null || true)"
case "$FUNNEL_NOW" in
  *"8443"*)
    echo "funnel TCP 8443 already configured"
    ;;
  *)
    sudo tailscale funnel --bg --tcp=8443 tcp://localhost:22 2>&1 | sed 's/^/  /'
    ;;
esac

echo "── 4. verify ──"
tailscale funnel status 2>&1 | sed 's/^/  /'
echo
echo "Done. Backup SSH path (works from anywhere, no Tailscale needed):"
echo "  ssh -p 8443 clutchd@clutchd-1.tail14cfb9.ts.net"
