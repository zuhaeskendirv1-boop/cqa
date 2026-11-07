#!/usr/bin/env bash
# One-command start (Linux/macOS). Windows: run.bat
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo ">> http://localhost:${PORT:-8000}"
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
