#!/usr/bin/env bash
set -euo pipefail

cd /app

echo "[grove] running migrations…"
alembic upgrade head

echo "[grove] starting uvicorn on port ${GROVE_PORT:-8080}"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${GROVE_PORT:-8080}" \
    --proxy-headers \
    --forwarded-allow-ips='*'
