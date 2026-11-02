#!/usr/bin/env bash
# One-time setup for macOS and Linux. Run from the repo root:  bash scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.10 or newer is required. Install it from python.org, then run this again."
  exit 1
fi

echo "1/5  Creating the Python virtual environment (backend/.venv)"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null

echo "2/5  Installing Python packages"
pip install -r requirements.txt

echo "3/5  Preparing backend/.env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "     Created backend/.env. Add your ANTHROPIC_API_KEY there to turn on Claude."
else
  echo "     backend/.env already exists, leaving it alone."
fi

echo "4/5  Creating the database and a fresh synthetic shift"
python manage.py migrate --noinput
python manage.py seed_demo

echo "5/5  Building the React app"
cd "$ROOT/frontend"
if command -v npm >/dev/null 2>&1; then
  npm install
  npm run build
elif [ -f dist/index.html ]; then
  echo "     npm not found, using the prebuilt frontend/dist that came in the zip."
else
  echo "     npm not found and no prebuilt app. Install Node.js 20 or newer, then run: cd frontend && npm install && npm run build"
fi

echo
echo "Done. Start the app with:  bash scripts/start.sh"
echo "Then open http://localhost:8000"
