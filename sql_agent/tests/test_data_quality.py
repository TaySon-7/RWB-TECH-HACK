"""Destructive fixture setup is allowed only in an explicitly named *_test DB."""
import os
from pathlib import Path

import psycopg2
import pytest

from sql_agent.db import Database


@pytest.mark.skipif(not os.getenv('QUALITY_TEST_DATABASE_URL'),reason='Requires a dedicated *_test database')
def test_quarantine_field_unknowns_duplicates_and_kept_anomaly():
    dsn=os.environ['QUALITY_TEST_DATABASE_URL']
    conn=psycopg2.connect(dsn)
    assert conn.info.dbname.endswith('_test'),'Never load synthetic raw fixtures into a working database'
    root=Path(__file__).resolve().parents[2]
    with conn,conn.cursor() as cur:
        cur.execute((root/'scripts/init/01_init.sql').read_text())
        cur.execute((root/'scripts/init/02_raw_ddl.sql').read_text())
        cur.execute("INSERT INTO raw.customers(customer_id,customer_unique_id,_source_file) VALUES ('c','person','fixture.csv')")
        cur.execute("INSERT INTO raw.sellers(seller_id,_source_file) VALUES ('s','fixture.csv')")
        cur.execute("INSERT INTO raw.products(product_id,_source_file) VALUES ('p','fixture.csv')")
        cur.execute("""INSERT INTO raw.orders(order_id,customer_id,order_status,order_purchase_timestamp,order_delivered_customer_date,_source_file)
            VALUES ('o','c','delivered','2018-01-01','2017-12-01','fixture.csv')""")
        cur.execute("""INSERT INTO raw.order_items(order_id,order_item_id,seller_id,product_id,price,freight_value,_source_file) VALUES
            ('o','1','s','p','10.00','2.00','fixture.csv'),('o','1','s','p','10.00','2.00','fixture.csv'),
            ('o',NULL,'s','p','99.00','2.00','fixture.csv'),('o','2',NULL,'p','5.00','1.00','fixture.csv'),
            ('absent','1','s','p','123.00','1.00','fixture.csv'),('o','3','s','p','garbage','1.00','fixture.csv')""")
        cur.execute("""INSERT INTO raw.order_payments(order_id,payment_sequential,payment_type,payment_installments,payment_value,_source_file)
            VALUES ('o','1','voucher','1','100','fixture.csv')""")
    source=(root/'model/build.sql').read_text()
    for name in ['tables.sql','audit.sql','load.sql','quality.sql','checks.sql','audit_views.sql']:
        source=source.replace('\\ir '+name,(root/'model'/name).read_text())
    source='\n'.join(line for line in source.splitlines() if not line.startswith('\\set'))
    with conn,conn.cursor() as cur:
        cur.execute(source)
        cur.execute('SELECT count(*) FROM raw.order_items')
        assert cur.fetchone()[0]==6
        cur.execute("SELECT count(*) FROM core.dq_issue WHERE check_id='delivery_before_purchase'")
        assert cur.fetchone()[0]==1
        cur.execute('SELECT count(*) FROM core.fct_order_item WHERE seller_id IS NULL')
        assert cur.fetchone()[0]==1
    conn.close()
    db=Database(dsn);_,tables=db.schema()
    result=db.execute("""SELECT sum(i.price) revenue FROM core.fct_order_item i
        JOIN core.fct_order o USING(order_id) WHERE i.seller_id='s' AND o.status_code='delivered'""",tables)
    assert result['rows'][0][0]==10
    report=db.quality_report(result)
    assert report['coverage']=='input_scope',report
    scope=next(s for s in report['sources'] if s['source']=='order_items')
    assert (scope['accepted'],scope['rejected'],scope['duplicates'])==(2,1,1)
    assert {r['action'] for r in scope['rules']}=={'reject_row','nullify_field','deduplicate'}
    assert report['affects_result']
