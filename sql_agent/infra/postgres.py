"""PostgreSQL execution: read-only, bounded and snapshot-consistent."""
from contextlib import contextmanager
import json
import time
import psycopg2
from .sql import validate_sql, referenced_tables

class Database:
    dialect = 'postgres'
    def __init__(self, dsn: str, timeout_ms: int = 15000, max_rows: int = 1000):
        self.dsn, self.timeout_ms, self.max_rows = dsn, timeout_ms, max_rows

    @contextmanager
    def connection(self):
        conn = psycopg2.connect(self.dsn, connect_timeout=5, application_name='marketplace_sql_agent')
        try:
            conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
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
                SELECT table_schema||'.'||table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema IN ('core','mart') ORDER BY table_schema,table_name,ordinal_position
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
        sql = validate_sql(sql, tables, self.dialect)
        started = time.monotonic()
        with self.connection() as conn:
            # Named cursor avoids buffering an unbounded result in the client.
            with conn.cursor(name='agent_result') as cur:
                cur.execute(sql)
                rows = cur.fetchmany(self.max_rows + 1)
                columns = [d.name for d in cur.description]
            with conn.cursor() as meta:
                meta.execute("SELECT to_regclass('audit.active_dataset') IS NOT NULL")
                if meta.fetchone()[0]:
                    meta.execute('SELECT version_id FROM audit.active_dataset')
                    version = meta.fetchone()
                else:
                    version = None
        return {'sql': sql, 'columns': columns, 'rows': rows[:self.max_rows],
                'truncated': len(rows) > self.max_rows,
                'tables': referenced_tables(sql, self.dialect),
                'elapsed_ms': int((time.monotonic()-started)*1000),
                'data_version': version[0] if version else None}

    @staticmethod
    def format_error(exc):
        if isinstance(exc, psycopg2.OperationalError):
            return 'Database connection unavailable; retry later.'
        if isinstance(exc, psycopg2.Error):
            return f'{exc.pgcode}: {exc.diag.message_primary}'[:700]
        return str(exc)[:700]

    def quality_report(self, query):
        from ..quality import postgres_report
        return postgres_report(self, query)
