#!/usr/bin/env bash
# Sobe o app em desenvolvimento.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

PY=.venv/bin/python
[[ -x "$PY" ]] || PY=.venv/Scripts/python.exe
[[ -x "$PY" ]] || PY=python

exec "$PY" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
