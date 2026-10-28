from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json


def cell(value):
    if value is None:
        return 'NULL'
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', '').replace('\n', '<br>')


def save_agent_result(state: dict, output: Path) -> dict:
    from .serialization import json_value
    folder = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    result = json_value(state['result'])
    result['artifact_dir'] = str(folder.resolve())
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Ответ агента', '', result['summary'], '']
    for query in result['queries']:
        (folder / (query['query_id'] + '.sql')).write_text(query['sql'], encoding='utf-8')
        columns, rows = query['columns'], query['rows']
        lines += ['| ' + ' | '.join(map(cell, columns)) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |']
        lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
        lines += ['', 'Версия данных: ' + str(query.get('data_version')), '',
                  '```sql', query['sql'], '```', '']
    lines += result.get('limitations', [])
    (folder / 'answer.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'artifact_dir': str(folder.resolve()), 'result': result}
