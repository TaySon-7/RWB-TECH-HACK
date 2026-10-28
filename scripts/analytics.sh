#!/usr/bin/env bash
# Repeatable deployment. Existing persistent data and secrets are preserved.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python3 scripts/init_analytics_env.py
DC=(docker compose --env-file .env.analytics -f docker-compose.analytics.yml)
case "${1:-up}" in
  up)
    bash scripts/download_data.sh
    "${DC[@]}" up -d --build --wait kafka clickhouse
    "${DC[@]}" build pipeline
    "${DC[@]}" run --rm --no-deps pipeline bootstrap
    "${DC[@]}" run --rm --no-deps pipeline ingest
    "${DC[@]}" up -d --wait grafana
    python3 scripts/provision_grafana.py
    "${DC[@]}" ps
    ;;
  check) "${DC[@]}" run --rm --no-deps pipeline check ;;
  replay) "${DC[@]}" run --rm --no-deps pipeline ingest --force ;;
  logs) "${DC[@]}" logs --tail=100 ;;
  stop) "${DC[@]}" stop ;;
  *) echo 'Usage: bash scripts/analytics.sh [up|check|replay|logs|stop]' >&2; exit 1 ;;
esac
