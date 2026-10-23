#!/usr/bin/env sh
# Data refresh for PRODUCTION (run by cron on the backend server 172.28.80.51).
# Uses the backend image and .env.production; one refresh at a time (database lock).
set -eu
cd "$(dirname "$0")"
mkdir -p logs
log="logs/refresh-$(date +%Y%m%d-%H%M%S).log"
echo "Refresh started $(date)" >> "$log"
if docker compose run --rm -T backend python -m backend.update_dashboard_data >> "$log" 2>&1; then
    echo "Refresh succeeded $(date)" >> "$log"
else
    status=$?
    echo "Refresh FAILED with exit code $status $(date)" >> "$log"
    exit "$status"
fi
# Keep 30 days of refresh logs.
find logs -name 'refresh-*.log' -mtime +30 -delete
