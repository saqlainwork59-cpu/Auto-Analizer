#!/bin/sh
# Start command for single-container hosting (Render free tier): one instance runs the
# database migrations, the market-data/signal worker and the API.
set -e
alembic upgrade head
(
  while true; do
    python -m app.worker || true
    echo "[render-start] worker exited; restarting in 5s"
    sleep 5
  done
) &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips '*' --workers 1
