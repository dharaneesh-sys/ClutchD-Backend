#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

cd "$BACKEND_DIR"

# Set PYTHONPATH so alembic can import app.models
export PYTHONPATH="${PYTHONPATH:-}:$BACKEND_DIR"

echo "🚀 Applying pending migrations..."
alembic upgrade head

echo "✅ Database is up to date at head revision."
