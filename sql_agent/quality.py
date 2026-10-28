"""Deterministic input-scope auditing. Unsupported SQL gets an explicit source-level report."""
import json

from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from .serialization import json_value

SOURCES = {'fct_order': 'orders', 'fct_order_item': 'order_items'}
CANDIDATES = {'fct_order': 'order_candidates', 'fct_order_item': 'item_candidates'}


def input_scopes(sql, dialect='postgres', audit_schema='audit'):
    """Extract physical FROM/JOIN/WHERE scopes before GROUP/HAVING/ORDER/LIMIT.

    This is deliberately not advertised as full result-row lineage for arbitrary SQL.
    CTEs are inspected at their physical leaves. Correlated/derived joins are skipped.
    """
    tree = parse_one(sql, read=dialect)
    result = []
    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Select):
            continue
        sources = {alias: pair[1] for alias, pair in scope.selected_sources.items()}
        if any(not isinstance(s, exp.Table) for s in sources.values()):
            continue
        if scope.expression.find(exp.Exists) or scope.expression.find(exp.Subquery):
            continue
        for alias, source in sources.items():
            if source.db != 'core' or source.name not in CANDIDATES:
                continue
            candidate = scope.expression.copy()
            for key in ['group', 'having', 'qualify', 'order', 'limit', 'offset', 'windows', 'distinct']:
                candidate.set(key, None)
            candidate.set('expressions', [exp.column('_record_id', table=alias),
                                          exp.column('_disposition', table=alias),
                                          exp.column('order_id', table=alias)])
            candidate = candidate.distinct()
            for table in list(candidate.find_all(exp.Table)):
                if table.db == 'core' and table.name == source.name and table.alias_or_name == alias:
                    table.replace(exp.Table(this=exp.to_identifier(CANDIDATES[source.name]),
                                            db=exp.to_identifier(audit_schema),
                                            alias=exp.TableAlias(this=exp.to_identifier(alias))))
            result.append((SOURCES[source.name], candidate.sql(dialect=dialect)))
            # Money/count summaries in fct_order depend on item admission too.
            if source.name == 'fct_order' and any(c.name in {'items_price','items_freight','items_price_cents','items_freight_cents','items_count','sellers_count'}
                                                 for c in scope.expression.find_all(exp.Column)):
                orders = candidate.copy()
                orders.set('expressions', [exp.column('order_id', table=alias)])
                q = (f'SELECT DISTINCT i._record_id,i._disposition,i.order_id '
                     f'FROM {audit_schema}.item_candidates i JOIN ({orders.sql(dialect=dialect)}) selected '
                     f'ON i.order_id=selected.order_id')
                result.append(('order_items', q))
    unique = {}
    for name, query in result:
        unique.setdefault(name, []).append(query)
    return [(name, ' UNION '.join('(' + q + ')' for q in dict.fromkeys(queries)))
            for name, queries in unique.items()]


def postgres_report(db, query):
    report = {'coverage': 'input_scope', 'data_version': query.get('data_version'),
              'sources': [], 'limitations': [], 'affects_result': False,
              'note': 'Охват входных записей до агрегации и ограничения вывода; не причинная интерпретация.'}
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('audit.active_dataset') IS NOT NULL")
            if not cur.fetchone()[0]:
                return unavailable('Учёт исходных записей ещё не собран. Запустите build_core.sh.')
            cur.execute('SELECT version_id FROM audit.active_dataset')
            row = cur.fetchone()
            version = row[0] if row else None
            if version != query.get('data_version'):
                return unavailable('Версия данных изменилась после расчёта. Выполните запрос заново.')
            cur.execute('SELECT source_files FROM audit.dataset WHERE version_id=%s', (version,))
            report['files'] = cur.fetchone()[0]
            scopes = input_scopes(query['sql'])
            if not scopes:
                report['coverage'] = 'source'
                report['limitations'].append('Для этой формы SQL доступен аудит источников, не точный охват строк.')
                cur.execute('SELECT source_table,disposition,count(*) FROM audit.source_record WHERE version_id=%s GROUP BY 1,2', (version,))
                report['source_totals'] = cur.fetchall()
                return json_value(report)
            for source, scope_sql in scopes:
                cur.execute(f'''WITH scope AS MATERIALIZED ({scope_sql}), records AS (
                    SELECT r.* FROM audit.source_record r WHERE r.version_id=%s
                     AND r.record_id IN (SELECT _record_id FROM scope)
                ) SELECT jsonb_build_object(
                    'source',%s,
                    'accepted',(SELECT count(*) FROM records WHERE disposition='accepted'),
                    'rejected',(SELECT count(*) FROM records WHERE disposition='rejected'),
                    'duplicates',(SELECT count(*) FROM records WHERE disposition='duplicate'),
                    'rules',(SELECT coalesce(jsonb_agg(x),'[]') FROM (
                        SELECT i.rule_id,i.field_name,i.action,i.message,count(DISTINCT i.record_id) records
                        FROM audit.issue i JOIN records r USING(version_id,record_id)
                        GROUP BY 1,2,3,4 ORDER BY records DESC) x),
                    'examples',(SELECT coalesce(jsonb_agg(x),'[]') FROM (
                        SELECT record_id,source_file,source_ref,entity_key,payload,disposition
                        FROM records WHERE disposition<>'accepted' ORDER BY record_id LIMIT 5) x)
                )''', (version, source))
                item = cur.fetchone()[0]
                report['sources'].append(item)
                if item['rejected']:
                    report['affects_result'] = True
                # Field-level unknowns are retained, and disclosed separately from rejected rows.
                if any(r['action'] == 'nullify_field' for r in item['rules']):
                    report['affects_result'] = True
            cur.execute('''SELECT source_table,count(*) FROM audit.source_record
                WHERE version_id=%s AND disposition='rejected'
                AND (nullif(payload->>'order_id','') IS NULL
                  OR (source_table='order_items' AND nullif(payload->>'seller_id','') IS NULL))
                GROUP BY source_table''', (version,))
            report['unattributed_source_rejections'] = dict(cur.fetchall())
            if report['unattributed_source_rejections']:
                report['limitations'].append('В источнике есть отклонённые записи без ключей атрибуции; их принадлежность фильтрам запроса неизвестна.')
            # Keep anomaly warnings distinct from exclusions.
            cur.execute('SELECT check_id,count(*) FROM core.dq_issue GROUP BY check_id ORDER BY 2 DESC')
            report['source_warnings'] = [{'rule':r[0], 'records':r[1], 'scope':'whole_core'} for r in cur.fetchall()]
    except Exception as exc:
        return unavailable('Не удалось установить точный охват аудита: ' + db.format_error(exc))
    return json_value(report)


def unavailable(message):
    return {'coverage': 'unavailable', 'sources': [], 'limitations': [message], 'affects_result': True}


def clickhouse_report(db, query):
    report = {'coverage': 'source', 'data_version': query.get('data_version'), 'sources': [],
              'limitations': [], 'affects_result': False}
    try:
        status = db._load_status()
        if status and status[0] != 'completed':
            return unavailable('Публикация нового набора ещё не завершена; аудит и расчёт нельзя сопоставить.')
        version = db._query('SELECT toString(max(source_version)) FROM ops.load_runs')['data'][0][0]
        if version != query.get('data_version'):
            return unavailable('Версия изменилась после расчёта. Выполните SQL заново.')
        manifests = int(db._query("SELECT count() FROM system.tables WHERE database='ops' AND name='load_manifests'")['data'][0][0])
        if manifests:
            files = db._query('SELECT files FROM ops.load_manifests FINAL WHERE source_version=(SELECT max(source_version) FROM ops.load_runs) LIMIT 1')['data']
            report['files'] = json.loads(files[0][0]) if files else []
        result = db._query('SELECT check_id,count() FROM core.dq_issue GROUP BY check_id ORDER BY count() DESC')
        report['source_warnings'] = [{'rule':r[0], 'records':r[1], 'scope':'whole_core'} for r in result['data']]
        exists = int(db._query("SELECT count() FROM system.tables WHERE database='ops' AND name='rejected_rows'")['data'][0][0])
        if not exists:
            report['limitations'].append('Загрузчик ClickHouse пока не предоставляет построчный карантин.')
            report['affects_result'] = True
            return report
        scopes = input_scopes(query['sql'], 'clickhouse', 'ops')
        if not scopes:
            report['limitations'].append('Для этой формы SQL показаны ограничения источников, без точного построчного охвата.')
            return report
        for source, scope_sql in scopes:
            counts = db._query(f'SELECT _disposition,uniqExact(_record_id) FROM ({scope_sql}) GROUP BY _disposition')['data']
            item = {'source': source, 'accepted': 0, 'rejected': 0, 'duplicates': 0, 'rules': [], 'examples': []}
            for disposition, count in counts:
                item['duplicates' if disposition == 'duplicate' else disposition] = int(count)
            rejected = db._query(f'''SELECT record_id,source_file,row_number,reason,payload
                FROM ops.rejected_rows FINAL WHERE source_version=(SELECT max(source_version) FROM ops.load_runs) AND record_id IN
                (SELECT _record_id FROM ({scope_sql}) WHERE _disposition!='accepted') LIMIT 5''')['data']
            item['examples'] = [dict(zip(['record_id','source_file','row_number','reason','payload'],r)) for r in rejected]
            rules = db._query(f'''SELECT reason,count() FROM ops.rejected_rows FINAL
                WHERE source_version=(SELECT max(source_version) FROM ops.load_runs) AND record_id IN
                (SELECT _record_id FROM ({scope_sql}) WHERE _disposition!='accepted') GROUP BY reason''')['data']
            item['rules'] = [{'rule_id':r[0].split(':')[0], 'message':r[0], 'records':int(r[1]),
                              'action':'deduplicate' if r[0].startswith('exact_duplicate:') else 'reject_row'} for r in rules]
            report['sources'].append(item)
            if item['rejected']:
                report['affects_result'] = True
        report['coverage'] = 'input_scope'
        unknown = db._query('''SELECT source_table,count() FROM ops.rejected_rows FINAL
             WHERE source_version=(SELECT max(source_version) FROM ops.load_runs)
             AND disposition='rejected'
             AND (order_id='' OR (source_table='order_items' AND seller_id='')) GROUP BY source_table''')['data']
        report['unattributed_source_rejections'] = {r[0]:int(r[1]) for r in unknown}
        if unknown:
            report['limitations'].append('Есть исходные отклонения с неизвестной принадлежностью продавцу/периоду; показаны отдельно.')
    except Exception as exc:
        return unavailable('Построчный аудит недоступен: ' + db.format_error(exc))
    return report
