import json
import os
import re
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sql_agent.db import Database, validate_sql
from sql_agent.graph import build_graph
from sql_agent.quality import input_scopes
from sql_agent.service import AgentService
from sql_agent.settings import Settings


def call(name, **args):
    return AIMessage(content='', tool_calls=[{'id': 'call_' + name, 'name': name, 'args': args}])


class FakeDB:
    dialect = 'postgres'

    def __init__(self):
        self.calls = []

    def schema(self):
        return json.dumps({'columns': [['core.fct_order','order_id','text']]}), {'core.fct_order'}

    def execute(self, sql, tables):
        self.calls.append(sql)
        validate_sql(sql, tables)
        if 'bad_' in sql:
            raise ValueError('column does not exist: ' + sql)
        return {'sql': sql, 'columns': ['n'], 'rows': [[10]], 'truncated': False,
                'tables': ['core.fct_order'], 'data_version': 'fixture-v1'}

    def quality_report(self, query):
        return {'coverage':'input_scope','sources':[],'affects_result':False,'limitations':[]}


class ScriptedModel:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.messages = []

    def invoke(self, messages, tools):
        self.messages.append(messages)
        step = next(self.steps)
        return step(messages) if callable(step) else step


def query_id(messages):
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            payload = json.loads(msg.content)
            if 'query_id' in payload:
                return payload['query_id']
    raise AssertionError('No executed query')


def quality(messages):
    return call('quality_report', query_id=query_id(messages))


def finish(messages):
    return call('finish_answer', summary='Получено 10 заказов.', query_ids=[query_id(messages)], limitations=[])


def successful_steps():
    return [call('query_sql', sql='SELECT count(*) AS n FROM core.fct_order'), quality, finish]


@pytest.mark.parametrize('query', [
    'DELETE FROM core.fct_order', 'SELECT 1; DROP TABLE core.fct_order',
    'WITH x AS (DELETE FROM core.fct_order RETURNING *) SELECT * FROM x',
    'SELECT * INTO new_table FROM core.fct_order', 'SELECT * FROM core.fct_order FOR UPDATE',
    'SELECT * FROM raw.orders', 'SELECT * FROM pg_catalog.pg_authid', 'SELECT * FROM fct_order',
    'SELECT pg_sleep(5)', "SELECT set_config('transaction_read_only','off',true)",
    "SELECT pg_catalog.pg_read_file('/etc/passwd')", 'SELECT * FROM core.nonexistent',
    'SELECT * FROM other.core.fct_order', "SELECT * FROM url('https://example.com')",
])
def test_reject_unsafe_sql(query):
    with pytest.raises(ValueError):
        validate_sql(query, {'core.fct_order'})


def test_business_queries_are_accepted():
    readme = Path(__file__).resolve().parents[2] / 'docs/answers/README.md'
    queries = re.findall(r'```sql\n(.*?)\n```', readme.read_text(), re.S)
    tables = {'fct_order', 'fct_order_item', 'dim_seller', 'dim_product', 'dim_category'}
    assert len(queries) == 5
    for query in queries:
        assert validate_sql(query, tables).endswith(';')


def test_agent_uses_results_repairs_and_saves_evidence(tmp_path):
    db = FakeDB()
    model = ScriptedModel([call('query_sql',sql='SELECT bad_column FROM core.fct_order'), *successful_steps()])
    state = build_graph(db, model, tmp_path).invoke({'question':'Сколько заказов?'})
    assert len(db.calls) == 2
    assert state['result']['status'] == 'completed'
    assert 'does not exist' in str(model.messages[1])
    result = state['result']['queries'][0]
    assert result['rows'] == [[10]] and result['quality']['coverage'] == 'input_scope'
    folder = Path(state['artifact_dir'])
    assert json.loads((folder/'result.json').read_text())['queries'][0]['query_id'] == result['query_id']
    assert (folder/(result['query_id']+'.sql')).exists()


def test_cannot_finish_with_invented_query_or_without_quality():
    def premature(messages):
        return call('finish_answer',summary='10',query_ids=[query_id(messages)],limitations=[])
    model = ScriptedModel([call('finish_answer',summary='100',query_ids=['invented'],limitations=[]),
                           successful_steps()[0], premature, quality, finish])
    state = build_graph(FakeDB(),model).invoke({'question':'Вопрос'})
    assert state['result']['queries'][0]['rows'] == [[10]]
    assert 'Неизвестные query_ids' in str(model.messages[1])
    assert 'quality_report' in str(model.messages[3])


def test_clarification_resumes_same_dialogue_without_query_before_answer():
    db = FakeDB()
    model = ScriptedModel([call('ask_user',question='За какой год?',options=['2017','2018']),*successful_steps()])
    graph = build_graph(db,model,checkpointer=InMemorySaver())
    config = {'configurable':{'thread_id':'dialogue'}}
    initial = graph.invoke({'question':'За август'},config)
    assert initial['__interrupt__'][0].value['question'] == 'За какой год?'
    assert not db.calls
    result = graph.invoke(Command(resume='2018'),config)
    assert result['result']['status']=='completed'
    assert '2018' in str(model.messages[1])


def test_bad_output_and_model_failure_are_bounded():
    model = ScriptedModel([AIMessage(content='plain')] * 3)
    state = build_graph(FakeDB(),model).invoke({'question':'Вопрос'})
    assert state['result']['status']=='failed'
    assert len(model.messages)==3


def test_sql_error_budget_prevents_more_database_calls():
    db = FakeDB()
    steps = [call('query_sql',sql=f'SELECT bad_{i} FROM core.fct_order') for i in range(7)]
    state=build_graph(db,ScriptedModel(steps),max_attempts=2,max_tool_calls=5).invoke({'question':'Вопрос'})
    assert len(db.calls)==2
    assert state['result']['status']=='failed'


def test_schema_failure_is_returned_as_result(tmp_path):
    class Broken(FakeDB):
        def schema(self):
            raise RuntimeError('unavailable')
    state=build_graph(Broken(),None,tmp_path).invoke({'question':'Вопрос'})
    assert state['result']['status']=='failed'
    assert (Path(state['artifact_dir'])/'result.json').exists()


def test_partial_status_comes_from_code_not_model():
    class Partial(FakeDB):
        def quality_report(self,q):
            return {'coverage':'source','sources':[],'limitations':['Точный охват не установлен.'],'affects_result':False}
    state=build_graph(Partial(),ScriptedModel(successful_steps())).invoke({'question':'Вопрос'})
    assert state['result']['status']=='partial'
    assert state['result']['limitations']==['Точный охват не установлен.']


def test_scope_respects_seller_status_and_inspects_items_of_order_totals():
    scopes=dict(input_scopes("SELECT sum(i.price) FROM core.fct_order_item i JOIN core.fct_order o USING(order_id) WHERE i.seller_id='s' AND o.status_code='delivered'"))
    assert 'audit.item_candidates' in scopes['order_items']
    assert "i.seller_id = 's'" in scopes['order_items']
    assert 'SUM' not in scopes['order_items']
    assert 'order_items' in dict(input_scopes("SELECT sum(items_price) FROM core.fct_order WHERE status_code='delivered'"))


def test_monthly_cte_scope_and_clickhouse_aggregate_functions():
    sql="WITH unused AS (SELECT 1), sales AS (SELECT sum(items_price) n FROM core.fct_order) SELECT * FROM sales"
    assert {name for name,_ in input_scopes(sql)}=={'orders','order_items'}
    validate_sql('SELECT countIf(items_count>0),sumIf(items_price,items_count>0),uniqExact(order_id) FROM core.fct_order',
                 {'core.fct_order'},'clickhouse')


def test_service_ownership_and_async_resume(tmp_path):
    service=AgentService(Settings(runtime_dir=tmp_path),db=FakeDB(),model=ScriptedModel(successful_steps()))
    try:
        cid=service.submit('Сколько заказов?',owner='alice')
        with pytest.raises(KeyError):
            service.read(cid,owner='bob')
        deadline=time.monotonic()+5
        while service.read(cid,owner='alice')['status']=='running' and time.monotonic()<deadline:
            time.sleep(.01)
        assert service.read(cid,owner='alice')['result']['status']=='completed'
    finally:
        service.close()


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Set TEST_DATABASE_URL for local PostgreSQL')
def test_real_postgres_limits_readonly_and_audit():
    db=Database(os.environ['TEST_DATABASE_URL'],max_rows=3)
    _,tables=db.schema()
    result=db.execute("SELECT order_id FROM core.fct_order WHERE status_code='delivered' ORDER BY order_id",tables)
    assert len(result['rows'])==3 and result['truncated']
    report=db.quality_report(result)
    assert report['coverage']=='input_scope',report
    assert report['sources'][0]['accepted']==96478
    with db.connection() as conn,conn.cursor() as cur:
        cur.execute('SHOW transaction_read_only')
        assert cur.fetchone()[0]=='on'


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Set TEST_DATABASE_URL for the original public dataset')
def test_all_five_controls_through_agent_graph():
    from decimal import Decimal
    queries=re.findall(r'```sql\n(.*?)\n```',(Path(__file__).resolve().parents[2]/'docs/answers/README.md').read_text(),re.S)
    db=Database(os.environ['TEST_DATABASE_URL'],max_rows=200)
    results=[]
    for sql in queries:
        state=build_graph(db,ScriptedModel([call('query_sql',sql=sql),quality,finish])).invoke({'question':'Контроль с известным SQL, без LLM'})
        assert state['result']['status']!='failed'
        q=state['result']['queries'][0]
        assert q['quality']['coverage']=='input_scope',q['quality']
        results.append(q['rows'])
    assert len(results[0])==24
    assert sum(Decimal(r[2]) for r in results[0])==Decimal('13221498.11')
    assert sum(Decimal(r[2])==0 for r in results[0])==1
    assert Decimal(results[1][0][-1])==Decimal('226987.93')
    assert Decimal(results[2][0][0])==Decimal('159.88')
    assert int(results[3][0][0])==96096 and Decimal(results[3][0][2])==Decimal('3.12')
    assert len(results[4])==74 and Decimal(results[4][0][1])==Decimal('1233131.72')
    assert sum(Decimal(r[1]) for r in results[4])==Decimal('13221498.11')
