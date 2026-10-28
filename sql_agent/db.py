"""Schema discovery and bounded, read-only query execution."""
from contextlib import contextmanager
import json

import psycopg2
from sqlglot import exp, parse
from sqlglot.optimizer.scope import traverse_scope

ALLOWED_FUNCTIONS = {
    'SUM', 'AVG', 'COUNT', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'ROUND',
    'CAST', 'TRY_CAST', 'EXTRACT', 'TIMESTAMP_TRUNC', 'DATE_TRUNC',
    'TIME_TO_STR', 'TO_CHAR', 'GENERATE_SERIES', 'GENERATE_TIMESTAMP_ARRAY',
    'EXPLODING_GENERATE_SERIES',
    'LOWER', 'UPPER', 'TRIM', 'LENGTH', 'ABS', 'CONCAT', 'CONCAT_WS',
    'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'LAG', 'LEAD', 'FIRST_VALUE',
    'LAST_VALUE', 'ARRAY_AGG', 'BOOL_AND', 'BOOL_OR', 'PERCENTILE_CONT',
    'PERCENTILE_DISC', 'CURRENT_DATE', 'CURRENT_TIMESTAMP', 'DATE',
    'CEIL', 'FLOOR', 'GREATEST', 'LEAST', 'STDDEV', 'VARIANCE',
    'CASE', 'IF', 'AND', 'OR',  # SQL syntax also represented as Func nodes.
}


def validate_sql(sql: str, tables: set[str]) -> str:
    statements = [s for s in parse(sql, read='postgres') if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError('Only one SELECT/CTE query is allowed')
    tree = statements[0]
    forbidden = ('Insert', 'Update', 'Delete', 'Create', 'Drop', 'Alter', 'Command', 'Copy', 'Into', 'Lock', 'Transaction')
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise ValueError(f'Forbidden SQL operation: {type(node).__name__}')
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in ALLOWED_FUNCTIONS:
                raise ValueError(f'Function is not allowed: {name}')
        # Do not permit schema-qualified function calls (including user functions).
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise ValueError('Qualified function calls are not allowed')
    # Scope analysis distinguishes actual tables from CTE aliases.
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                if source.catalog or source.db != 'core' or source.name not in tables:
                    raise ValueError(f'Only known core tables are allowed: {source.sql()}')
    return sql.strip().rstrip(';') + ';'


class Database:
    def __init__(self, dsn: str, timeout_ms: int = 15000, max_rows: int = 1000):
        self.dsn, self.timeout_ms, self.max_rows = dsn, timeout_ms, max_rows

    @contextmanager
    def connection(self):
        conn = psycopg2.connect(self.dsn, connect_timeout=5, application_name='marketplace_sql_agent')
        try:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(self.timeout_ms),))
                cur.execute("SET LOCAL search_path TO pg_catalog, core")
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def schema(self) -> tuple[str, set[str]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema='core' ORDER BY table_name, ordinal_position
            """)
            rows = cur.fetchall()
            if not rows:
                raise ValueError('core is empty: run bash scripts/build_core.sh first')
            cur.execute("""
                SELECT c.relname, pg_get_constraintdef(k.oid)
                FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='core' AND k.contype IN ('p','f','u') ORDER BY c.relname,k.conname
            """)
            constraints = cur.fetchall()
        return json.dumps({'columns': rows, 'constraints': constraints}, ensure_ascii=False), {r[0] for r in rows}

    def execute(self, sql: str, tables: set[str]) -> dict:
        sql = validate_sql(sql, tables)
        with self.connection() as conn:
            # Named cursor avoids buffering an unbounded result in the client.
            with conn.cursor(name='agent_result') as cur:
                cur.execute(sql)
                rows = cur.fetchmany(self.max_rows + 1)
                columns = [d.name for d in cur.description]
        return {'sql': sql, 'columns': columns, 'rows': rows[:self.max_rows],
                'truncated': len(rows) > self.max_rows}
