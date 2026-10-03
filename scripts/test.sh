#!/bin/sh
set -eu
mkdir -p data
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m pytest "$@"
fi
exec python -m pytest "$@"
