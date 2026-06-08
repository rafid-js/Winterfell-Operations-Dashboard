#!/bin/bash
set -e

echo "=== Winterfell startup ==="

# Start cron worker in background
echo "Starting cron worker..."
python -m orchestrator.cron_worker &
CRON_PID=$!
echo "Cron worker PID: $CRON_PID"

# Start web server in foreground (keeps the container alive)
echo "Starting web server on port ${PORT:-5000}..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers 2 \
  --timeout 120 \
  orchestrator.app:app
