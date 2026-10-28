#!/usr/bin/env bash
# Assemble the saved SQL files for execution inside the PostgreSQL container.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(docker-compose)
fi
# psql runs on stdin, so expand only the known, local include directives.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    '\ir tables.sql') cat model/tables.sql ;;
    '\ir load.sql') cat model/load.sql ;;
    '\ir quality.sql') cat model/quality.sql ;;
    '\ir checks.sql') cat model/checks.sql ;;
    *) printf '%s\n' "$line" ;;
  esac
done < model/build.sql | "${DC[@]}" exec -T postgres psql -X -U hack -d marketplace -v ON_ERROR_STOP=1
