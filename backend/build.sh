#!/usr/bin/env bash
# Render build step: install, collect static files, migrate, and seed the demo once.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Seed only an empty database, so redeploys never wipe what is already there.
python manage.py shell -c "import sys; from triage.models import Patient; sys.exit(0 if Patient.objects.exists() else 1)" \
  || python manage.py seed_demo
