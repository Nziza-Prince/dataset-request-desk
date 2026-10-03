#!/bin/sh
set -eu
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_demo --with-episodes
.venv/bin/python manage.py collectstatic --noinput
exec .venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2
