"""Small HTTP adapter for the same ClickHouse used by Grafana."""
import json
import time
from decimal import Decimal

import httpx

from .sql import referenced_tables, validate_sql


class ClickHouseDatabase:
    dialect = 'clickhouse'

    def __init__(self, url, user, password, timeout_ms=15000, max_rows=200):
        self.url, self.user, self.password = url, user, password
        self.timeout_ms, self.max_rows = timeout_ms, max_rows

    def _query(self, sql):
        with httpx.Client(timeout=self.timeout_ms / 1000 + 5) as client:
            response = client.post(self.url, content=sql,
                                   params={'readonly': 1, 'max_execution_time': max(1, self.timeout_ms // 1000),
                                           'default_format': 'JSONCompact'},
                                   auth=(self.user, self.password))
        if response.status_code != 200:
            raise ValueError(response.text.split('Stack trace')[0][:900])
        result = json.loads(response.text, parse_float=Decimal)
        if result.get('exception'):
            raise ValueError(result['exception'][:900])
        return result

    def schema(self):
        result = self._query("SELECT database||'.'||table, name, type FROM system.columns "
                             "WHERE database IN ('core','mart') ORDER BY database,table,position")
        columns = result['data']
        if not columns:
            raise ValueError('ClickHouse core is empty; run the analytics importer first.')
        return json.dumps({'columns': columns, 'constraints': [], 'dialect': self.dialect},
                          ensure_ascii=False), {r[0] for r in columns}

    def execute(self, sql, tables):
        sql = validate_sql(sql, tables, self.dialect)
        started = time.monotonic()
        before = self._load_status()
        if before and before[0] in ('loading','failed') and any(t.startswith('core.') for t in referenced_tables(sql,self.dialect)):
            raise ValueError('Core is being refreshed or the last import failed; retry after the validated publication.')
        result = self._query(f'SELECT * FROM ({sql.rstrip(";")}) AS agent_result LIMIT {self.max_rows + 1}')
        version = self._query('SELECT toString(max(source_version)) FROM ops.load_runs')['data']
        after = self._load_status()
        if before != after:
            raise ValueError('Dataset publication changed during the calculation; repeat the query.')
        return {'sql': sql, 'columns': [c['name'] for c in result['meta']],
                'rows': result['data'][:self.max_rows],
                'truncated': len(result['data']) > self.max_rows,
                'tables': referenced_tables(sql, self.dialect),
                'elapsed_ms': int((time.monotonic()-started)*1000),
                'data_version': version[0][0] if version else None}

    def _load_status(self):
        exists = int(self._query("SELECT count() FROM system.tables WHERE database='ops' AND name='load_state'")['data'][0][0])
        if not exists:
            return None
        rows = self._query('SELECT argMax(status,changed_at),toString(max(source_version)) FROM ops.load_state '
                           'WHERE source_version=(SELECT max(source_version) FROM ops.load_state)')['data']
        return rows[0] if rows else None

    def quality_report(self, query):
        from ..quality import clickhouse_report
        return clickhouse_report(self, query)

    @staticmethod
    def format_error(exc):
        if isinstance(exc, httpx.TransportError):
            return 'ClickHouse connection unavailable; retry later.'
        return str(exc)[:900]
