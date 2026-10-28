"""End-to-end checks against published ClickHouse and Grafana, no extra deps."""
import base64
import json
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent.parent
settings = dict(line.split('=', 1) for line in (ROOT / '.env.analytics').read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)
DC = ['docker', 'compose', '--env-file', '.env.analytics', '-f', 'docker-compose.analytics.yml']


def sql(text):
    result = subprocess.run(DC + ['exec', '-T', 'clickhouse', 'sh', '-c',
        'exec clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"'],
        input=text, text=True, cwd=ROOT, check=True, capture_output=True)
    return result.stdout.strip()


def grafana(endpoint, body=None):
    credential = base64.b64encode(f"admin:{settings['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()
    request = Request('http://127.0.0.1:' + settings.get('GRAFANA_PORT', '3000') + endpoint,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Authorization': 'Basic ' + credential, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Grafana {endpoint}: {error.read().decode()}') from None


controls = sql('''SELECT sum(revenue_cents),sum(freight_cents),sum(orders_count)
    FROM mart.sales_daily WHERE day >= '2016-09-01' AND day < '2018-09-01' ''')
assert controls == '1322149811\t220296554\t96478', controls
months = sql('''SELECT count(),countIf(revenue=0) FROM (
    SELECT toStartOfMonth(day) AS month,sum(revenue_cents) AS revenue
    FROM mart.sales_daily WHERE day >= '2016-09-01' AND day < '2018-09-01' GROUP BY month)''')
assert months == '24\t1', months
assert sql('SELECT count() FROM core.dim_customer') == '96096'
assert sql('SELECT countIf(orders_count >= 2) FROM core.dim_customer') == '2997'
assert sql("SELECT round(sum(items_price_cents + items_freight_cents)/count()/100,2) FROM core.fct_order WHERE status_code='delivered'") == '159.88'
assert sql('''SELECT sum(i.price_cents) AS revenue FROM core.fct_order_item AS i
    INNER JOIN core.fct_order AS o ON i.order_id=o.order_id
    WHERE o.status_code='delivered' GROUP BY i.seller_id ORDER BY revenue DESC LIMIT 1''') == '22698793'
assert sql('SELECT count() FROM core.dim_category') == '74'
assert sql('SELECT count() FROM core.fct_review_order') == '99224'
assert sql('SELECT count() FROM core.fct_review') == '98410'
assert grafana('/api/health')['database'] == 'ok'
health = grafana('/api/datasources/uid/capibara-clickhouse/health')
assert health['status'] == 'OK', health
dashboard = grafana('/api/dashboards/uid/capibara-commerce')['dashboard']
for panel in dashboard['panels']:
    if 'targets' not in panel:
        continue
    body = {'from': '1472688000000', 'to': '1535759999999', 'queries': []}
    for target in panel['targets']:
        body['queries'].append({**target, 'intervalMs': 86400000, 'maxDataPoints': 1000})
    response = grafana('/api/ds/query', body)
    for result in response['results'].values():
        assert not result.get('error'), (panel['title'], result)
        assert result.get('frames'), (panel['title'], result)
        values = result['frames'][0]['data']['values']
        assert values and values[0], (panel['title'], values)
        if panel['type'] == 'stat':
            expected = 13221498.11 if panel['id'] == 2 else 2202965.54
            assert abs(float(values[0][0])-expected) < 0.005, values
        else:
            assert len(values[0]) == 24, (panel['title'], len(values[0]))
print('PASS: entity controls, BRL totals, 24 months including zero month, read-only datasource, every Grafana panel.')
