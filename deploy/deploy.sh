#!/usr/bin/env bash
# Called by GitHub Actions or manually from a release directory on the server.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec 9>/home/fedya/rwb-tech-hack/deploy.lock
flock -w 900 9
python3 scripts/init_analytics_env.py
bash scripts/analytics.sh up
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:3000/api/health', timeout=15) as response:
    health=json.load(response)
assert health['database']=='ok', health
print('Grafana health check passed.')
PY
printf '%s\n' "$(date -u +%FT%TZ) ${DEPLOY_REVISION:-manual}" > /home/fedya/rwb-tech-hack/last-successful-deploy
