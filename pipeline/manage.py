"""CSV -> typed Protobuf -> partitioned Kafka -> native ClickHouse consumers.

Only SELECT/DDL and publication SQL use HTTP. Source rows enter ClickHouse solely
through the Kafka engine, making the integration path observable and testable.
"""
import argparse
import base64
import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
from google.protobuf.json_format import MessageToDict

import marketplace_pb2 as wire
from schema import TABLES
from quality import snapshot

ROOT = Path(__file__).resolve().parent.parent
EPOCH = datetime(1970, 1, 1)


def query(sql):
    auth = base64.b64encode(
        f"{os.environ.get('CLICKHOUSE_USER', 'pipeline')}:{os.environ['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()
    request = Request(os.environ.get('CLICKHOUSE_URL', 'http://clickhouse:8123'),
                      data=sql.encode(), headers={'Authorization': 'Basic ' + auth})
    try:
        with urlopen(request, timeout=180) as response:
            return response.read().decode().strip()
    except Exception as exc:
        detail = exc.read().decode() if hasattr(exc, 'read') else str(exc)
        raise RuntimeError(detail) from None


def apply_sql(filename):
    # Contracts and SQL contain no semicolons in literals.
    text = '\n'.join(line for line in (ROOT / filename).read_text().splitlines()
                     if not line.lstrip().startswith('--'))
    for statement in text.split(';'):
        if statement.strip():
            query(statement)


def convert(value, kind):
    if kind in ('str', 'id'):
        if kind == 'id' and not value.strip():
            raise ValueError(f'Invalid identifier: {value!r}')
        return value.strip() if kind == 'id' else value
    if kind == 'money':
        cents = Decimal(value) * 100
        if cents != cents.to_integral_value() or cents < 0:
            raise ValueError(f'Invalid money: {value!r}')
        return int(cents)
    if kind == 'time':
        if not value:
            return 0
        delta = datetime.fromisoformat(value) - EPOCH
        return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    if kind == 'optional_int' and value == '':
        return -1
    return int(value)


def event_for(row, table, version):
    _, message, filename, keys, fields = table
    values = {name: convert(row[source], kind) for source, name, kind in fields}
    for name in keys:
        if isinstance(values[name], int) and values[name] <= 0:
            raise ValueError(f'Invalid key {name}')
    if table[0] == 'orders' and not values.get('order_purchase_timestamp_ms'):
        raise ValueError('Missing purchase timestamp')
    event = getattr(wire, message)(**values, source_version=version, source_file=filename)
    key = ':'.join(str(values[name]) for name in keys).encode()
    return key, event


def dataset_info():
    prepared = snapshot(ROOT, event_for)
    return prepared['hash'], prepared['expected']


def bootstrap():
    admin = AdminClient({'bootstrap.servers': os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')})
    futures = admin.create_topics([
        NewTopic('capibara.v1.' + table[0], num_partitions=3, replication_factor=1)
        for table in TABLES
    ])
    for topic, future in futures.items():
        try:
            future.result(60)
        except Exception as exc:
            if 'TOPIC_ALREADY_EXISTS' not in str(exc):
                raise
    apply_sql('clickhouse/01_ingestion.sql')
    apply_sql('clickhouse/02_core.sql')
    password = os.environ['GRAFANA_DB_PASSWORD']
    if not password.isalnum():
        raise ValueError('GRAFANA_DB_PASSWORD must contain only letters and numbers')
    query(f"CREATE USER IF NOT EXISTS grafana_reader IDENTIFIED WITH sha256_password BY '{password}'")
    query('ALTER USER grafana_reader SETTINGS readonly = 1, max_execution_time = 30 MIN 1 MAX 300 CHANGEABLE_IN_READONLY')
    query('GRANT SELECT ON mart.* TO grafana_reader')
    query('GRANT SELECT ON core.* TO grafana_reader')
    from audit import bootstrap_audit
    bootstrap_audit(query)
    agent_password = os.environ.get('CLICKHOUSE_AGENT_PASSWORD', password)
    if not agent_password.isalnum():
        raise ValueError('CLICKHOUSE_AGENT_PASSWORD must be alphanumeric')
    query(f"CREATE USER IF NOT EXISTS agent_reader IDENTIFIED WITH sha256_password BY '{agent_password}'")
    query('ALTER USER agent_reader SETTINGS readonly=1, max_threads=4, max_memory_usage=800000000, max_execution_time=30 MIN 1 MAX 60 CHANGEABLE_IN_READONLY')
    for database in ['core','mart','ops']:
        query(f'GRANT SELECT ON {database}.* TO agent_reader')
    print('Kafka topics, Protobuf consumers, core and read-only Grafana user ready.', flush=True)


def counts():
    result = {}
    for name, *_ in TABLES:
        result[name] = int(query(f'SELECT count() FROM raw.{name} FINAL'))
    return result


def wait_for_ingestion(expected, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        actual = counts()
        if actual == expected:
            return
        print('Waiting for native Kafka consumers:', actual, flush=True)
        time.sleep(5)
    raise RuntimeError(f'Ingestion timeout. Expected {expected}, actual {actual}')


def validate(expected):
    actual = counts()
    if actual != expected:
        raise RuntimeError(f'Row counts differ: {actual} != {expected}')
    checks = {
        'orphan_customers': '''SELECT count() FROM raw.orders AS o FINAL
            LEFT ANTI JOIN raw.customers AS c FINAL ON o.customer_id=c.customer_id''',
        'orphan_items': '''SELECT count() FROM raw.order_items AS i FINAL
            LEFT ANTI JOIN raw.orders AS o FINAL ON i.order_id=o.order_id''',
        'orphan_products': '''SELECT count() FROM raw.order_items AS i FINAL
            LEFT ANTI JOIN raw.products AS p FINAL ON i.product_id=p.product_id''',
        'orphan_sellers': '''SELECT count() FROM raw.order_items AS i FINAL
            LEFT ANTI JOIN raw.sellers AS s FINAL ON i.seller_id=s.seller_id''',
        'orphan_payments': '''SELECT count() FROM raw.order_payments AS p FINAL
            LEFT ANTI JOIN raw.orders AS o FINAL ON p.order_id=o.order_id''',
        'orphan_reviews': '''SELECT count() FROM raw.order_reviews AS r FINAL
            LEFT ANTI JOIN raw.orders AS o FINAL ON r.order_id=o.order_id''',
        'conflicting_review_attributes': '''SELECT count() FROM (
            SELECT review_id FROM raw.order_reviews FINAL GROUP BY review_id
            HAVING uniqExact(tuple(review_score,review_comment_title,review_comment_message,
                                  review_creation_date_ms,review_answer_timestamp_ms)) > 1)''',
        'order_money': '''SELECT abs(
            (SELECT sum(items_price_cents) FROM core.fct_order) -
            (SELECT sum(price_cents) FROM raw.order_items FINAL))''',
        'order_freight': '''SELECT abs(
            (SELECT sum(items_freight_cents) FROM core.fct_order) -
            (SELECT sum(freight_cents) FROM raw.order_items FINAL))''',
    }
    for label, sql in checks.items():
        if int(query(sql)) != 0:
            raise RuntimeError(f'Quality check failed: {label}')
    print('All entity counts, mandatory references and money checks passed.', flush=True)


def publish():
    query('DROP TABLE IF EXISTS mart.sales_daily_next')
    query('CREATE TABLE mart.sales_daily_next AS mart.sales_daily')
    query('''INSERT INTO mart.sales_daily_next
        SELECT d.date_key, coalesce(s.revenue_cents,0), coalesce(s.freight_cents,0),
               coalesce(s.orders_count,0)
        FROM core.dim_date AS d
        LEFT JOIN (
            SELECT purchase_date, sum(items_price_cents) AS revenue_cents,
                   sum(items_freight_cents) AS freight_cents, count() AS orders_count
            FROM core.fct_order WHERE status_code='delivered' GROUP BY purchase_date
        ) AS s ON d.date_key=s.purchase_date''')
    source = query("SELECT sum(items_price_cents),sum(items_freight_cents) FROM core.fct_order WHERE status_code='delivered'")
    target = query('SELECT sum(revenue_cents),sum(freight_cents) FROM mart.sales_daily_next')
    if source != target:
        raise RuntimeError(f'Mart money check failed: {source} != {target}')
    query('EXCHANGE TABLES mart.sales_daily AND mart.sales_daily_next')
    query('DROP TABLE mart.sales_daily_next')
    print('Published validated dashboard snapshot:', target, 'centavos.', flush=True)


def ingest(force=False):
    started = time.monotonic()
    prepared = snapshot(ROOT, event_for)
    fingerprint, expected = prepared['hash'], prepared['expected']
    latest = query('SELECT dataset_hash,toString(source_version) FROM ops.load_runs ORDER BY source_version DESC LIMIT 1').split('\t')
    state = query('SELECT toString(max(source_version)),argMax(status,changed_at) FROM ops.load_state').split('\t')
    already_loaded = (len(latest) == 2 and latest[0] == fingerprint and state == [latest[1], 'completed'])
    if already_loaded and counts() == expected and not force:
        print('Source snapshot unchanged; keeping Kafka offsets and existing data.', flush=True)
        validate(expected)
        publish()
        return
    version = time.time_ns()
    from audit import begin_load, save_rejections, finish_load
    begin_load(query, version)
    save_rejections(query, prepared['rejections'], version)
    producer = Producer({
        'bootstrap.servers': os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
        'enable.idempotence': True, 'acks': 'all', 'compression.type': 'lz4',
        'linger.ms': 20, 'batch.size': 262144, 'queue.buffering.max.kbytes': 65536,
    })
    failures = []
    total_rows = total_wire = total_json = 0

    def delivered(error, message):
        if error:
            failures.append(str(error))

    for table, path in prepared['files']:
        name = table[0]
        relative = str(path.relative_to(ROOT / 'data'))
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for number, row in enumerate(csv.DictReader(handle), 1):
                if (relative, number) in prepared['bad_locators']:
                    continue
                key, event = event_for(row, table, version)
                event.source_file = relative
                payload = event.SerializeToString()
                json_size = len(json.dumps(MessageToDict(event, preserving_proto_field_name=True),
                                           ensure_ascii=False, separators=(',', ':')).encode())
                while True:
                    try:
                        producer.produce('capibara.v1.' + name, key=key, value=payload, on_delivery=delivered)
                        break
                    except BufferError:
                        producer.poll(0.1)
                total_rows += 1
                total_wire += len(payload)
                total_json += json_size
                if total_rows % 10000 == 0:
                    producer.poll(0)
        print(f'Queued {relative}', flush=True)
    remaining = producer.flush(180)
    if remaining or failures:
        raise RuntimeError(f'Kafka delivery failed: pending={remaining}, errors={failures[:3]}')
    # For replay, count equality is insufficient: wait for all latest versions.
    deadline = time.monotonic() + 180
    while any(int(query(f'SELECT count() FROM raw.{name} FINAL WHERE source_version={version}')) != count
              for name, count in expected.items()):
        if time.monotonic() > deadline:
            raise RuntimeError('Not all current-version events were consumed')
        time.sleep(2)
    # All current rows arrived: remove superseded snapshot rows, including deletions.
    for name, *_ in TABLES:
        query(f'ALTER TABLE raw.{name} DELETE WHERE source_version < {version} SETTINGS mutations_sync=1')
    validate(expected)
    publish()
    elapsed = time.monotonic() - started
    query(f"INSERT INTO ops.load_runs (dataset_hash,source_version,rows,protobuf_bytes,json_bytes,elapsed_seconds) VALUES ('{fingerprint}',{version},{total_rows},{total_wire},{total_json},{elapsed})")
    finish_load(query, version, prepared)
    print(json.dumps({'rows': total_rows, 'protobuf_bytes': total_wire,
                      'equivalent_json_bytes': total_json,
                      'payload_reduction_percent': round(100*(1-total_wire/total_json), 1),
                      'end_to_end_seconds': round(elapsed, 2)}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['bootstrap', 'ingest', 'check', 'publish'])
    parser.add_argument('--force', action='store_true', help='Replay the same source snapshot to verify deduplication')
    args = parser.parse_args()
    try:
        if args.command == 'bootstrap':
            bootstrap()
        elif args.command == 'ingest':
            ingest(args.force)
        elif args.command == 'check':
            validate(dataset_info()[1])
        else:
            publish()
    except Exception:
        if args.command == 'ingest':
            try:
                query("INSERT INTO ops.load_state(source_version,status) SELECT max(source_version),'failed' FROM ops.load_state HAVING argMax(status,changed_at)='loading'")
            except Exception:
                pass
        raise
