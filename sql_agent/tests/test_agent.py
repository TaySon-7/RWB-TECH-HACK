import os
import re
from pathlib import Path

import pytest

from sql_agent.db import Database, validate_sql
from sql_agent.graph import build_graph
from sql_agent.llm import SQLDraft


class SequenceModel:
    def __init__(self, sqls):
        self.sqls = iter(sqls)
        self.histories = []

    def generate(self, question, schema, history):
        self.histories.append(list(history))
        return SQLDraft(sql=next(self.sqls), assumptions='Доставленные заказы; без доставки.')


class FakeDB:
    def __init__(self):
        self.calls = 0

    def schema(self):
        return 'core.fct_order(order_id,status_code)', {'fct_order'}

    def execute(self, sql, tables):
        self.calls += 1
        validate_sql(sql, tables)
        if 'bad_column' in sql:
            raise ValueError('column bad_column does not exist')
        return {'sql': sql, 'columns': ['n'], 'rows': [(96478,)], 'truncated': False}


@pytest.mark.parametrize('query', [
    'DELETE FROM core.fct_order',
    'SELECT 1; DROP TABLE core.fct_order',
    'WITH changed AS (DELETE FROM core.fct_order RETURNING *) SELECT * FROM changed',
    'SELECT * INTO new_table FROM core.fct_order',
    'SELECT * FROM core.fct_order FOR UPDATE',
    'SELECT * FROM raw.orders',
    'SELECT * FROM pg_catalog.pg_authid',
    'SELECT * FROM fct_order',
    'SELECT pg_sleep(5)',
    "SELECT set_config('transaction_read_only','off',true)",
    "SELECT pg_catalog.pg_read_file('/etc/passwd')",
    'SELECT * FROM core.nonexistent',
    'SELECT * FROM other.core.fct_order',
])
def test_reject_unsafe_sql(query):
    with pytest.raises(ValueError):
        validate_sql(query, {'fct_order'})


@pytest.mark.parametrize('query', [
    'SELECT count(*) FROM core.fct_order',
    "WITH x AS (SELECT * FROM core.fct_order) SELECT count(*) FROM x",
    "SELECT date_trunc('month', purchase_date), SUM(items_price) FROM core.fct_order GROUP BY 1",
    "SELECT round(avg(items_price),2) FROM core.fct_order",
    "SELECT coalesce(sum(items_price),0) FROM core.fct_order WHERE status_code='delivered'",
    "SELECT CASE WHEN status_code='delivered' THEN 1 ELSE 0 END FROM core.fct_order",
])
def test_allow_analytics(query):
    assert validate_sql(query, {'fct_order'}).endswith(';')


def test_all_five_documented_business_queries_are_accepted():
    readme = Path(__file__).resolve().parents[2] / 'docs/answers/README.md'
    queries = re.findall(r'```sql\n(.*?)\n```', readme.read_text(), re.S)
    tables = {'fct_order', 'fct_order_item', 'dim_seller', 'dim_product', 'dim_category'}
    assert len(queries) == 5
    for query in queries:
        validate_sql(query, tables)


def test_repair_sends_error_to_model_and_saves_artifacts(tmp_path):
    db = FakeDB()
    model = SequenceModel(['SELECT bad_column FROM core.fct_order', 'SELECT count(*) FROM core.fct_order'])
    state = build_graph(db, model, tmp_path).invoke({'question': 'Сколько доставленных заказов?'})
    assert state['error'] is None
    assert db.calls == 2
    assert 'bad_column' in model.histories[1][0]['error']
    folder = Path(state['artifact_dir'])
    assert (folder / 'query.sql').read_text().startswith('SELECT count(*)')
    assert '96478' in (folder / 'answer.md').read_text()
    assert (folder / 'attempt_01.sql').exists()
    assert (folder / 'trace.json').exists()


def test_failed_queries_stop_at_budget_and_save_failure(tmp_path):
    db = FakeDB()
    model = SequenceModel(['SELECT bad_column FROM core.fct_order'] * 2)
    state = build_graph(db, model, tmp_path, max_attempts=2).invoke({'question': 'Вопрос'})
    assert state['error']
    assert state['attempts'] == db.calls == 2
    assert 'Не удалось' in (Path(state['artifact_dir']) / 'answer.md').read_text()


def test_schema_failure_still_saves_artifacts(tmp_path):
    class BrokenDB:
        def schema(self):
            raise RuntimeError('database unavailable')
    state = build_graph(BrokenDB(), None, tmp_path).invoke({'question': 'Вопрос'})
    assert state['fatal']
    assert (Path(state['artifact_dir']) / 'query.sql').exists()


def test_model_failure_still_saves_artifacts(tmp_path):
    class BrokenModel:
        def generate(self, *args):
            raise RuntimeError('model unavailable')
    state = build_graph(FakeDB(), BrokenModel(), tmp_path).invoke({'question': 'Вопрос'})
    assert state['fatal']
    assert 'model unavailable' in (Path(state['artifact_dir']) / 'answer.md').read_text()


def test_empty_query_is_explained(tmp_path):
    state = build_graph(FakeDB(), SequenceModel(['']), tmp_path).invoke({'question': 'Нет данных'})
    assert state['fatal']
    assert state['attempts'] == 1


def test_truncated_and_empty_results(tmp_path):
    class ResultsDB(FakeDB):
        def execute(self, sql, tables):
            return {'sql': sql, 'columns': ['n'], 'rows': [], 'truncated': False}
    state = build_graph(ResultsDB(), SequenceModel(['SELECT 1']), tmp_path).invoke({'question': 'Вопрос'})
    assert not state['error']
    assert '0 строк' in (Path(state['artifact_dir']) / 'answer.md').read_text()
    class TruncatedDB(FakeDB):
        def execute(self, sql, tables):
            return {'sql': sql, 'columns': ['n'], 'rows': [(1,)], 'truncated': True}
    state = build_graph(TruncatedDB(), SequenceModel(['SELECT 1']), tmp_path).invoke({'question': 'Вопрос'})
    assert 'обрезан' in (Path(state['artifact_dir']) / 'answer.md').read_text()


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='Set TEST_DATABASE_URL for local PostgreSQL')
def test_real_postgres_repair_and_row_limit(tmp_path):
    db = Database(os.environ['TEST_DATABASE_URL'], max_rows=3)
    model = SequenceModel([
        'SELECT bad_column FROM core.fct_order',
        "SELECT count(*) AS orders FROM core.fct_order WHERE status_code='delivered'",
    ])
    state = build_graph(db, model, tmp_path).invoke({'question': 'Сколько доставленных заказов?'})
    assert state['error'] is None
    assert state['attempts'] == 2
    assert state['rows'] == [(96478,)]
    _, tables = db.schema()
    result = db.execute('SELECT order_id FROM core.fct_order ORDER BY order_id', tables)
    assert len(result['rows']) == 3
    assert result['truncated']
    # The SQL guard is complemented by PostgreSQL's read-only transaction.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute('SHOW transaction_read_only')
        assert cur.fetchone()[0] == 'on'
