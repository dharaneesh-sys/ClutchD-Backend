#!/usr/bin/env bash
# clutchd-api-alerts — ALERT watchdog for the production ClutchD API.
#
# The self-heal watchdog (/usr/local/bin/clutchd-watchdog.sh) already
# restarts clutchd-api when it dies — silently. This companion does NOT
# heal anything; it notices and ALERTS, so crashes are visible:
#
#   1. service crashed   → systemctl is-active clutchd-api != active
#   2. staying unhealthy → GET :8000/health not 200 (skips the first few
#                          seconds after a restart while uvicorn boots)
#   3. flapping          → too many self-heal restarts in the last 15 min
#
# State machine (persisted in /var/lib/clutchd-api-alerts/state):
#   ok            → (failure seen)             → alert + state=failing
#   failing       → (still failing, ≥REALERT_EVERY runs later) → re-alert
#   failing       → (healthy again)            → recovery notice + state=ok
#
# Runs every 2 min via clutchd-api-alerts.timer. Read-only: never restarts,
# never writes outside its state dir and the alert log.

set -uo pipefail

ALERT_TO="${ALERT_TO:-dharaneesh8a@gmail.com}"
STATE_DIR="/var/lib/clutchd-api-alerts"
STATE_FILE="$STATE_DIR/state"
FAIL_COUNT_FILE="$STATE_DIR/fail_count"
LOG="/var/log/clutchd-api-alerts.log"

# Health grace period: uvicorn takes a few seconds to bind after a restart.
# If the service is active AND was (re)started < 90s ago, don't alert yet.
GRACE_SECONDS=90
# Re-alert cadence while still failing (in runs; timer is 2 min → every 10 min).
REALERT_EVERY=5
# Flapping threshold: restarts of clutchd-api within this window…
FLAP_WINDOW_MIN=15
# …more than this many → flapping alert.
FLAP_MAX_RESTARTS=3

SMTP_HOST="smtp-relay.brevo.com"
SMTP_PORT=587
SMTP_USER="b925db001@smtp-brevo.com"
SMTP_PASS="$(grep -E '^SMTP_PASSWORD=' /home/clutchd/ClutchD-Backend/backend/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' )"
SMTP_FROM="ClutchD Alerts <b925db001@smtp-brevo.com>"

log()  { echo "$(date '+%F %T') $*" >> "$LOG"; }
state() { cat "$STATE_FILE" 2>/dev/null || echo "ok"; }
set_state() { mkdir -p "$STATE_DIR"; echo "$1" > "$STATE_FILE"; }
fail_count() { cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0; }
set_fail_count() { echo "$1" > "$FAIL_COUNT_FILE"; }

send_alert() {
  # send_alert "<subject>" "<body>"
  if [ -z "$SMTP_PASS" ]; then
    log "ALERT (no SMTP password — logged only): $1 | $2"
    return
  fi
  python3 - "$SMTP_HOST" "$SMTP_PORT" "$SMTP_USER" "$SMTP_PASS" "$SMTP_FROM" "$ALERT_TO" "$1" "$2" <<'PYEOF'
import smtplib, ssl, sys
host, port, user, pw, sender, to, subject, body = sys.argv[1:9]
msg = f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n\r\n{body}"
try:
    with smtplib.SMTP(host, int(port), timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.sendmail(sender, [to], msg.encode("utf-8"))
    print("sent")
except Exception as e:
    print(f"error: {e}")
    sys.exit(1)
PYEOF
}

healthy() {
  # 200 on /health (either route) == healthy
  local code
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null)
  [ "$code" = "200" ]
}

service_active() {
  systemctl is-active --quiet clutchd-api
}

in_grace_period() {
  # Active for less than GRACE_SECONDS? (systemd shows e.g. "active (running) for 12s")
  systemctl show clutchd-api -p ActiveEnterTimestamp --value 2>/dev/null | {
    read -r ts
    [ -z "$ts" ] && return 1
    local started now diff
    started=$(date -d "$ts" +%s 2>/dev/null) || return 1
    now=$(date +%s)
    diff=$(( now - started ))
    [ "$diff" -lt "$GRACE_SECONDS" ]
  }
}

restart_count_recent() {
  # grep -c prints 0 with exit 1 on no match — neutralize so `set -o pipefail`
  # style failures never feed a multiline/empty value into [ -gt ].
  local n
  n=$(journalctl -u clutchd-api --since "-${FLAP_WINDOW_MIN}min" 2>/dev/null \
    | grep -c "Started ClutchD API" || true)
  [ -z "$n" ] && n=0
  echo "$n"
}

reason=""
if ! service_active; then
  reason="service DOWN (systemctl is-active: $(systemctl is-active clutchd-api 2>/dev/null))"
elif ! healthy; then
  if in_grace_period; then
    log "health bad but within ${GRACE_SECONDS}s startup grace — skipping"
    exit 0
  fi
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null)
  reason="health check failing (GET /health → $code)"
fi

NOW=$(date '+%F %T')
HOST=$(hostname)
if [ -z "$reason" ]; then
  # ── Everything fine ──
  if [ "$(state)" = "failing" ]; then
    send_alert "✅ ClutchD API RECOVERED ($HOST)" \
"ClutchD API is healthy again on $HOST.

Recovered at: $NOW
It was failing on the previous check. No action needed.

— clutchd-api-alerts"
    log "RECOVERED — recovery notice sent to $ALERT_TO"
  fi
  set_state "ok"
  set_fail_count 0
  exit 0
fi

# ── Something is wrong ──
# Flap detection: self-heal watchdog restarting over and over?
flaps=$(restart_count_recent | head -1)
case "$flaps" in ''|*[!0-9]*) flaps=0 ;; esac
if [ "$flaps" -gt "$FLAP_MAX_RESTARTS" ]; then
  reason="$reason; FLAPPING: $flaps restarts in ${FLAP_WINDOW_MIN}min (self-heal loop?)"
fi

if [ "$(state)" != "failing" ]; then
  # First failure → alert immediately.
  set_state "failing"
  set_fail_count 1
  send_alert "🔴 ClutchD API DOWN on $HOST" \
"ClutchD API problem detected at $NOW.

Problem: $reason

The self-heal watchdog may restart it automatically, but check:
  ssh clutchd
  systemctl status clutchd-api
  journalctl -u clutchd-api -n 50
  curl -s http://127.0.0.1:8000/health

— clutchd-api-alerts"
  log "ALERT sent ($reason)"
else
  # Still failing → re-alert every REALERT_EVERY runs.
  n=$(( $(fail_count) + 1 ))
  set_fail_count "$n"
  if [ $(( n % REALERT_EVERY )) -eq 0 ]; then
    mins=$(( n * 2 ))
    send_alert "🔴 ClutchD API STILL DOWN (${mins}min) on $HOST" "ClutchD API still failing at $NOW — $mins since first alert.

Problem: $reason

— clutchd-api-alerts"
    log "RE-ALERT sent (run #$n, $reason)"
  else
    log "still failing (run #$n): $reason"
  fi
fi
