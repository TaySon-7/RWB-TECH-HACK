"""Create local secrets once. Never overwrite existing environment values."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
target = root / '.env.analytics'
existing = target.read_text() if target.exists() else ''
keys = {line.split('=', 1)[0] for line in existing.splitlines() if '=' in line}
values = {
    'CLICKHOUSE_PASSWORD': secrets.token_hex(24),
    'GRAFANA_DB_PASSWORD': secrets.token_hex(24),
    'CLICKHOUSE_AGENT_PASSWORD': secrets.token_hex(24),
    'GRAFANA_ADMIN_PASSWORD': secrets.token_urlsafe(24),
    'GRAFANA_VIEWER_PASSWORD': secrets.token_urlsafe(24),
    'GRAFANA_BIND': os.environ.get('GRAFANA_BIND', '127.0.0.1'),
    'GRAFANA_PORT': '3000',
}
with target.open('a') as output:
    if existing and not existing.endswith('\n'):
        output.write('\n')
    for key, value in values.items():
        if key not in keys:
            output.write(f'{key}={value}\n')
target.chmod(0o600)
print('Analytics environment ready; secrets are stored in .env.analytics (0600).')
