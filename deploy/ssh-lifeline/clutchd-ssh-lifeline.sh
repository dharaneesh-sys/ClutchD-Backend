#!/usr/bin/env bash
# clutchd-ssh-lifeline — fast guard for the two SSH paths to this box.
#
# The systemd drop-ins (tailscaled-restart.conf, ssh-restart.conf) make
# both daemons Restart=always — that covers kills/crashes within ~3s.
# This 30s watchdog covers what unit restarts cannot:
#   - tailscaled running but logged OUT (NeedsLogin) → re-auth with key
#   - tailscale interface broken (no 100.x addr) → restart tailscaled
#   - funnel missing → re-enable (API must stay reachable from the app)
#   - sshd running but not listening on :22 → restart ssh
#
# Alerts: failures are logged AND surfaced in the api-alerts log, so a
# broken SSH path is never silent. This script NEVER touches clutchd-api.

set -uo pipefail

LOG="/var/log/clutchd-ssh-lifeline.log"
TSKEY="/root/.clutchd-tskey"
FUNNEL_HOST="clutchd-1.tail14cfb9.ts.net"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

notify_api_alerts_log() {
  # Reuse the api-alerts log so one `tail` shows today's incidents.
  echo "$(date '+%F %T') [ssh-lifeline] $*" >> /var/log/clutchd-api-alerts.log 2>/dev/null || true
}

probl=0

# ── 1. tailscaled alive? (drop-in should handle it; belt & suspenders) ──
if ! systemctl is-active --quiet tailscaled; then
  log "tailscaled DOWN — restarting"
  notify_api_alerts_log "tailscaled was down — restarted by ssh-lifeline"
  systemctl restart tailscaled
  sleep 5
  probl=1
fi

# ── 2. logged in? ──
STATE=$(tailscale status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('BackendState','?'))" 2>/dev/null || echo "?")
# "Starting" is normal right after a restart (warming up) — never touch it,
# or the watchdog would fight the startup it just triggered.
if [ "$STATE" = "Starting" ]; then
  log "tailscale Starting (warming up) — leaving it alone"
  exit 0
fi
if [ "$STATE" != "Running" ]; then
  log "tailscale state=$STATE"
  if [ "$STATE" = "NeedsLogin" ] && [ -f "$TSKEY" ]; then
    log "re-authenticating with stored key"
    notify_api_alerts_log "tailscale needed re-login — auto re-authed with stored key"
    tailscale up --ssh --authkey="$(cat "$TSKEY")" >> "$LOG" 2>&1
    probl=1
  else
    notify_api_alerts_log "tailscale state=$STATE — NEEDS MANUAL AUTH (no usable key)"
    probl=1
  fi
fi

# ── 3. tailnet IP present? (daemon up but interface broken) ──
TSIP=$(tailscale ip -4 2>/dev/null | head -1)
if [ -z "$TSIP" ]; then
  log "no tailscale IPv4 — restarting tailscaled"
  notify_api_alerts_log "tailscale lost its IP — tailscaled restarted"
  systemctl restart tailscaled
  sleep 5
  probl=1
fi

# ── 4. API funnel (HTTPS 443) still serving? ──
# NOTE 1: capture-then-grep. `tailscale funnel status | grep -q` under
# pipefail returns 141 (SIGPIPE): grep exits after matching, tailscale
# gets EPIPE writing the rest, and a SUCCESSFUL match looks like failure.
# NOTE 2: match the "https://host" line SPECIFICALLY. The SSH funnel
# (tcp://…:8443) also contains the hostname, so a bare hostname match
# false-passes while the app-facing 443 funnel is gone (2026-09-21
# incident: 443 vanished from the serve config, this check said "ok",
# and the app could not reach the API at all).
FUNNEL_OUT="$(tailscale funnel status 2>/dev/null || true)"
case "$FUNNEL_OUT" in
  *"https://$FUNNEL_HOST"*) : ;;
  *)
    log "API funnel (https 443) MISSING — re-enabling"
    notify_api_alerts_log "https funnel 443 was gone — re-enabled by ssh-lifeline"
    tailscale funnel --bg --https=443 http://localhost:8000 >> "$LOG" 2>&1
    probl=1
    ;;
esac

# ── 4b. SSH funnel (raw TCP 8443 -> :22) still configured? ──
# The backup SSH path for when the tailnet itself is unreachable. Same
# capture-then-grep pattern as above.
SSH_FUNNEL_PORT="8443"
case "$FUNNEL_OUT" in
  *"$SSH_FUNNEL_PORT"*) : ;;
  *)
    log "ssh funnel tcp/$SSH_FUNNEL_PORT MISSING — re-enabling"
    notify_api_alerts_log "ssh funnel tcp/$SSH_FUNNEL_PORT was gone — re-enabled by ssh-lifeline"
    tailscale funnel --bg --tcp=$SSH_FUNNEL_PORT tcp://localhost:22 >> "$LOG" 2>&1
    probl=1
    ;;
esac

# ── 5. sshd alive AND listening? ──
if ! systemctl is-active --quiet ssh; then
  log "sshd DOWN — restarting"
  notify_api_alerts_log "sshd was down — restarted by ssh-lifeline"
  systemctl restart ssh
  sleep 3
  probl=1
fi
SS_OUT="$(ss -tln 2>/dev/null || true)"
case "$SS_OUT" in
  *":22 "*) : ;;
  *)
  log "nothing LISTENING on :22 — restarting ssh"
  notify_api_alerts_log ":22 stopped listening — ssh restarted"
  systemctl restart ssh
  sleep 3
  probl=1
  ;;
esac

[ "$probl" -eq 0 ] && log "ok" || true
exit 0
