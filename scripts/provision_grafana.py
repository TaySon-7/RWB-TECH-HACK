"""Create a separate jury viewer once; preserve credentials on later deploys."""
import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
settings = dict(line.split('=', 1) for line in (ROOT / '.env.analytics').read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)
auth = base64.b64encode(f"admin:{settings['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()
base = 'http://127.0.0.1:' + settings.get('GRAFANA_PORT', '3000')


def request(path, body=None):
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


try:
    viewer = request('/api/users/lookup?loginOrEmail=jury')
    if viewer.get('isAdmin'):
        raise RuntimeError('Existing jury account is an administrator; refusing to repurpose it.')
except HTTPError as error:
    if error.code != 404:
        raise
    request('/api/admin/users', {'name': 'Жюри Capibara', 'login': 'jury',
                                'password': settings['GRAFANA_VIEWER_PASSWORD'], 'OrgId': 1})
credential = base64.b64encode(f"jury:{settings['GRAFANA_VIEWER_PASSWORD']}".encode()).decode()
req = Request(base + '/api/user', headers={'Authorization': 'Basic ' + credential})
with urlopen(req, timeout=30) as response:
    user = json.load(response)
assert not user['isGrafanaAdmin'], user
req = Request(base + '/api/user/orgs', headers={'Authorization': 'Basic ' + credential})
with urlopen(req, timeout=30) as response:
    orgs = json.load(response)
assert any(org['orgId'] == 1 and org['role'] == 'Viewer' for org in orgs), orgs
print('Jury account ready: login jury, organization role Viewer; credentials preserved.')
