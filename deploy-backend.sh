#!/usr/bin/env bash
# ClutchD production deploy — pull latest backend and restart the REAL API.
#
# Topology (do not confuse):
#   Caddy :8080 → 127.0.0.1:8000  = bare-metal uvicorn (clutchd-api.service)  ← production
#   Docker api    → 127.0.0.1:8001  = opt-in test container (profile docker-api), NOT production
#
# Usage: ./deploy-backend.sh          (from ~/ClutchD-Backend on the server)

set -euo pipefail
cd "$(dirname "$0")"

echo "── git pull ──────────────"
git pull --ff-only

echo "── restart production API (systemd, :8000) ──"
echo 1907 | sudo -S systemctl restart clutchd-api 2>/dev/null \
  || sudo -n systemctl restart clutchd-api \
  || sudo systemctl restart clutchd-api

for i in $(seq 1 15); do
  if curl -sf -m 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 \
     || curl -sf -m 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "✅ production API healthy on :8000"
    exit 0
  fi
  sleep 2
done

echo "❌ API did not become healthy — check: journalctl -u clutchd-api -n 50"
exit 1
