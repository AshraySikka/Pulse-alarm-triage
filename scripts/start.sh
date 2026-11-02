#!/usr/bin/env bash
# Start Sift on http://localhost:8000. Run from the repo root:  bash scripts/start.sh
# Add --fresh to generate a new synthetic shift first (handy right before a demo).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [ ! -d .venv ]; then
  echo "Run bash scripts/setup.sh first."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [ "${1:-}" = "--fresh" ]; then
  python manage.py seed_demo
fi

echo "Sift is starting on http://localhost:8000  (Ctrl+C to stop)"
python manage.py runserver 8000
