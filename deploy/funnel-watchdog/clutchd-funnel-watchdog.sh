#!/usr/bin/env bash
# clutchd-funnel-watchdog.sh — self-healing guard for the ClutchD public API.
#
# Problem it solves:
#   After a server reboot, the Tailscale Funnel registration on Tailscale's
#   public ingress edge can go stale: the node says "Funnel on" locally, but
#   every external TLS handshake is killed in <1s (HTTP 000). The app then
#   cannot reach the backend even though the server itself is perfectly
#   healthy. A full `systemctl restart tailscaled` forces a fresh edge
#   registration and heals it.
#
# Detection:
#   1. Local API must answer 200 on http://127.0.0.1:8000/health
#      (if the API itself is down, restarting tailscaled won't help).
#   2. Public edge must answer 200 on https://$FUNNEL_HOST/health, probed
#      with curl --resolve pinned to the PUBLIC ingress IPs from 1.1.1.1
#      (never MagicDNS — the server would short-circuit to its own tailnet
#      IP and mask a stale edge; that happened during diagnosis).
#
# Remediation:
#   systemctl restart tailscaled → re-assert funnel if needed → re-probe.
#   Max 2 heal attempts per run, max $MAX_RESTARTS_PER_HOUR restarts per
#   hour (prevents flapping during a genuine Tailscale regional outage).
#
# Exit codes: 0 healthy/recovered · 2 edge stale, gave up (human needed)
#             3 local API down (not our problem) · 4 cannot probe (no dig)
#
# Env overrides (for testing): FUNNEL_HOST, LOCAL_HEALTH_URL, PROBE_TIMEOUT,
#   PROBE_ROUNDS, FUNNEL_PORT, MAX_RESTARTS_PER_HOUR, FORCE_FAIL=1
#   (FORCE_FAIL simulates a stale edge to exercise the full heal path).
set -u

HOST="${FUNNEL_HOST:-clutchd-1.tail14cfb9.ts.net}"
LOCAL_URL="${LOCAL_HEALTH_URL:-http://127.0.0.1:8000/health}"
PUBLIC_URL="https://${HOST}/health"
TIMEOUT="${PROBE_TIMEOUT:-15}"
ROUNDS="${PROBE_ROUNDS:-2}"
PORT="${FUNNEL_PORT:-8080}"
MAX_RESTARTS_HR="${MAX_RESTARTS_PER_HOUR:-3}"
FORCE_FAIL="${FORCE_FAIL:-0}"
STATE_DIR="${STATE_DIR:-/var/lib/clutchd-funnel-watchdog}"
STATE_FILE="$STATE_DIR/restarts.log"
STATUS_FILE="$STATE_DIR/last-status"

log() {
    local line
    line="[funnel-watchdog] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
    echo "$line"
    echo "$line" >> "$STATUS_FILE" 2>/dev/null || { mkdir -p "$STATE_DIR"; echo "$line" >> "$STATUS_FILE"; }
}

# ── Probes ────────────────────────────────────────────────────────────────
probe_local() {
    local code
    code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$LOCAL_URL" 2>/dev/null)
    [ "$code" = "200" ]
}

# Public ingress IPs only — from a public resolver, never the local one.
# Filters out tailnet (100.64/10), RFC1918 and loopback just in case.
edge_ips() {
    command -v dig >/dev/null 2>&1 || return 1
    dig +short @1.1.1.1 "$HOST" A 2>/dev/null \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
        | grep -vE '^(100\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|127\.)' \
        | head -2
}

probe_edge() {
    local ips ip code
    if [ "$FORCE_FAIL" = "1" ]; then
        log "FORCE_FAIL=1 — simulating stale edge"
        return 1
    fi
    ips=$(edge_ips) || { log "no edge IPs resolved (dig missing or DNS down)"; return 1; }
    [ -n "$ips" ] || { log "no edge IPs resolved"; return 1; }
    for ip in $ips; do
        code=$(curl -s -m "$TIMEOUT" --resolve "${HOST}:443:${ip}" \
               -o /dev/null -w '%{http_code}' "$PUBLIC_URL" 2>/dev/null)
        log "edge probe ${ip}: HTTP ${code:-000}"
        [ "$code" = "200" ] && return 0
    done
    return 1
}

edge_ok() {
    local r
    for r in $(seq 1 "$ROUNDS"); do
        probe_edge && return 0
        [ "$r" -lt "$ROUNDS" ] && sleep 2
    done
    return 1
}

# ── Restart budget (anti-flap) ────────────────────────────────────────────
recent_restarts() {
    local now count
    now=$(date +%s)
    touch "$STATE_FILE"
    awk -v now="$now" '$1 ~ /^[0-9]+$/ && (now - $1) <= 3600' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
    count=$(wc -l < "$STATE_FILE")
    echo "$count"
}

# ── Heal ──────────────────────────────────────────────────────────────────
heal() {
    local n
    n=$(recent_restarts)
    if [ "$n" -ge "$MAX_RESTARTS_HR" ]; then
        log "CRIT: edge stale but restart budget exhausted (${n}/${MAX_RESTARTS_HR} in the last hour) — needs a human"
        exit 2
    fi
    log "edge stale while local API healthy — restarting tailscaled (restart $((n + 1))/${MAX_RESTARTS_HR} this hour)"
    date +%s >> "$STATE_FILE"
    systemctl restart tailscaled
    sleep 15
    if ! tailscale funnel status 2>/dev/null | grep -q "Funnel on"; then
        log "funnel not advertised after restart — re-publishing on port ${PORT}"
        tailscale funnel --bg "$PORT" >/dev/null 2>&1
        sleep 3
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────
main() {
    : > "$STATUS_FILE" 2>/dev/null || { mkdir -p "$STATE_DIR"; : > "$STATUS_FILE"; }

    if ! probe_local; then
        log "local API DOWN on 127.0.0.1:8000 — not touching tailscaled (different problem)"
        exit 3
    fi

    if [ "$FORCE_FAIL" = "1" ]; then
        log "test mode: skipping healthy check, forcing heal path"
    elif edge_ok; then
        log "OK: funnel edge healthy"
        exit 0
    else
        log "WARN: public funnel edge not answering (local API is fine) — stale edge suspected"
    fi

    heal
    if probe_edge; then
        log "RECOVERED: funnel edge healthy after tailscaled restart"
        exit 0
    fi

    log "still stale after first restart — trying once more"
    heal
    if probe_edge; then
        log "RECOVERED: funnel edge healthy after 2nd tailscaled restart"
        exit 0
    fi

    log "CRIT: funnel edge still stale after 2 restarts — escalate to a human"
    exit 2
}

main
