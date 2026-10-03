#!/bin/sh
set -eu
mkdir -p "$(dirname "${DATABASE_PATH:-data/desk.sqlite3}")"
python manage.py migrate --noinput
if [ "${SEED_DEMO:-0}" = "1" ]; then
    python manage.py seed_demo --with-episodes
fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60
