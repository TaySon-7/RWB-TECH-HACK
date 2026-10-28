"""Five fixed control queries through the actual read-only HTTP adapter; no LLM."""
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlglot import parse_one

from sql_agent.graph import build_graph
from sql_agent.service import database
from sql_agent.settings import Settings
from sql_agent.tests.test_agent import ScriptedModel, call, quality, query_id


@pytest.mark.skipif(os.getenv('TEST_CLICKHOUSE')!='1',reason='Run inside the analytics agent container')
def test_five_clickhouse_controls_and_audit():
    db=database(Settings())
    assert db.dialect=='clickhouse'
    original=re.findall(r'```sql\n(.*?)\n```',(Path(__file__).resolve().parents[2]/'docs/answers/README.md').read_text(),re.S)
    sqls=[parse_one(sql,read='postgres').sql(dialect='clickhouse') for sql in original]
    sqls[0]="""WITH months AS (
      SELECT DISTINCT month_start AS month FROM core.dim_date
      WHERE date_key>='2016-09-01' AND date_key<'2018-09-01'
    ), sales AS (
      SELECT toStartOfMonth(purchase_date) AS month,count() orders_count,sum(items_price) revenue
      FROM core.fct_order WHERE status_code='delivered'
      AND purchase_date>='2016-09-01' AND purchase_date<'2018-09-01' GROUP BY month
    ) SELECT toString(m.month) month,coalesce(s.orders_count,0) orders_count,
      coalesce(s.revenue,0) revenue FROM months m LEFT JOIN sales s ON m.month=s.month ORDER BY month"""
    sqls[2]=sqls[2].replace('COUNT(*) FILTER(WHERE lines_count = 0)','countIf(lines_count = 0)')
    sqls[3]=sqls[3].replace('COUNT(*) FILTER(WHERE orders_count >= 2)','countIf(orders_count >= 2)')
    sqls[4]=sqls[4].replace('SELECT c.category_key,','SELECT c.category_key AS category_key,')
    def finish(messages):
        return call('finish_answer',summary='Контрольный SQL проверен.',query_ids=[query_id(messages)],limitations=[])
    rows=[]
    for sql in sqls:
        state=build_graph(db,ScriptedModel([call('query_sql',sql=sql),quality,finish])).invoke({'question':'Контроль, известный SQL без LLM'})
        assert state['result']['status']!='failed',state['result']
        query=state['result']['queries'][0]
        assert query['quality']['coverage']=='input_scope',query['quality']
        assert query['data_version']
        rows.append(query['rows'])
    assert len(rows[0])==24 and sum(Decimal(r[2]) for r in rows[0])==Decimal('13221498.11')
    assert sum(Decimal(r[2])==0 for r in rows[0])==1
    assert Decimal(rows[1][0][-1])==Decimal('226987.93')
    assert Decimal(rows[2][0][0])==Decimal('159.88')
    assert int(rows[3][0][0])==96096 and Decimal(rows[3][0][2])==Decimal('3.12')
    assert len(rows[4])==74 and Decimal(rows[4][0][1])==Decimal('1233131.72')
    assert sum(Decimal(r[1]) for r in rows[4])==Decimal('13221498.11')
