"""Auxiliary import evidence and candidate views; the 14 core tables stay intact."""
import json


def bootstrap_audit(query):
    query('''CREATE TABLE IF NOT EXISTS ops.rejected_rows (
        source_version UInt64, record_id String, source_table LowCardinality(String),
        source_file String, row_number UInt64, disposition LowCardinality(String),
        reason String, payload String, order_id String, seller_id String
    ) ENGINE=ReplacingMergeTree ORDER BY(source_version,record_id)''')
    query('''CREATE TABLE IF NOT EXISTS ops.load_state (
        source_version UInt64,status LowCardinality(String),changed_at DateTime64(6) DEFAULT now64(6)
    ) ENGINE=MergeTree ORDER BY(source_version,changed_at)''')
    query('''CREATE TABLE IF NOT EXISTS ops.load_manifests (
        source_version UInt64,dataset_hash String,files String,accepted_counts String,
        rejected_count UInt64,duplicate_count UInt64,rules_version String
    ) ENGINE=ReplacingMergeTree ORDER BY source_version''')
    for table in ['fct_order','fct_order_item']:
        source = 'orders' if table == 'fct_order' else 'order_items'
        columns = [json.loads(line) for line in query(
            f"SELECT name,type FROM system.columns WHERE database='core' AND table='{table}' ORDER BY position FORMAT JSONEachRow").splitlines()]
        accepted_key = 'i.order_id' if source == 'orders' else 'i.order_id,i.order_item_id'
        expressions, accepted = [], []
        aliases = {'status_code':'order_status','source_customer_id':'customer_id',
                   'freight':'freight_value','shipping_limit_at':'shipping_limit_date'}
        dates = {'purchase_date':'order_purchase_timestamp','purchased_at':'order_purchase_timestamp',
                 'approved_at':'order_approved_at','carrier_at':'order_delivered_carrier_date',
                 'delivered_at':'order_delivered_customer_date','estimated_date':'order_estimated_delivery_date',
                 'shipping_limit_at':'shipping_limit_date'}
        available = {'order_id','order_item_id','seller_id','product_id','price','freight','shipping_limit_at',
                     'status_code','source_customer_id',*dates}
        for column in columns:
            name, kind = column['name'],column['type']
            nullable = kind if kind.startswith('Nullable(') else f'Nullable({kind})'
            key = name in {'order_id','seller_id','product_id','source_customer_id','customer_key','order_item_id'}
            if key:
                nullable = kind
            accepted.append(f'CAST(i.{name} AS {nullable}) AS {name}')
            if name not in available:
                value = f"CAST({repr('') if key else 'NULL'} AS {nullable})"
            else:
                field = dates.get(name, aliases.get(name,name))
                raw = f"JSONExtractString(payload,'{field}')"
                if name in dates:
                    value = f"parseDateTime64BestEffortOrNull({raw},3,'UTC')"
                    if name in {'purchase_date','estimated_date'}:
                        value = f'toDate({value})'
                elif 'Decimal' in kind:
                    value = f'toDecimal64OrNull({raw},2)'
                elif kind.startswith(('Int','UInt')):
                    value = f'toInt64OrNull({raw})'
                else:
                    value = f"nullIf({raw},'')"
                if key:
                    value = f"ifNull({value},{'0' if name=='order_item_id' else repr('')})"
                value = f"CAST({value} AS {nullable})"
            expressions.append(value + ' AS ' + name)
        query(f'''CREATE OR REPLACE VIEW ops.{'order_candidates' if source=='orders' else 'item_candidates'}
            DEFINER=CURRENT_USER SQL SECURITY DEFINER AS
            SELECT concat('accepted:',hex(MD5(toJSONString(tuple({accepted_key}))))) AS _record_id,
                   'accepted' AS _disposition,{','.join(accepted)} FROM core.{table} i
            UNION ALL SELECT record_id,toString(disposition),{','.join(expressions)} FROM ops.rejected_rows FINAL
            WHERE source_table='{source}'
             AND source_version=(SELECT max(source_version) FROM ops.load_runs)''')


def begin_load(query, version):
    query(f"INSERT INTO ops.load_state(source_version,status) VALUES ({version},'loading')")


def save_rejections(query, rows, version):
    if rows:
        payload = '\n'.join(json.dumps({'source_version': version,**r},ensure_ascii=False) for r in rows)
        query('INSERT INTO ops.rejected_rows FORMAT JSONEachRow\n'+payload)


def finish_load(query, version, prepared):
    rejected = sum(r['disposition']=='rejected' for r in prepared['rejections'])
    duplicates = sum(r['disposition']=='duplicate' for r in prepared['rejections'])
    row = {'source_version':version,'dataset_hash':prepared['hash'],
           'files':json.dumps(prepared['manifest'],ensure_ascii=False),
           'accepted_counts':json.dumps(prepared['expected']),
           'rejected_count':rejected,'duplicate_count':duplicates,'rules_version':'contracts-v1'}
    query('INSERT INTO ops.load_manifests FORMAT JSONEachRow\n'+json.dumps(row,ensure_ascii=False))
    query(f"INSERT INTO ops.load_state(source_version,status) VALUES ({version},'completed')")
