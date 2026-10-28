from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json


def cell(value):
    if value is None:
        return 'NULL'
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', '').replace('\n', '<br>')


def save_artifacts(state: dict, output: Path) -> dict:
    folder = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    success = not state.get('error')
    sql = state.get('sql') or '-- SQL was not generated. See answer.md.\n'
    (folder / 'query.sql').write_text(sql + '\n', encoding='utf-8')
    for attempt in state.get('history', []):
        (folder / f"attempt_{attempt['attempt']:02}.sql").write_text(attempt.get('sql', '') + '\n', encoding='utf-8')
    lines = ['# Ответ SQL-агента', '', '## Вопрос', '', state['question'], '',
             '## Статус', '', 'Запрос выполнен.' if success else 'Не удалось получить результат.', '',
             f"Попыток генерации: {state.get('attempts', 0)}.", '',
             '## Формула и допущения', '', state.get('assumptions') or 'Не определены.', '',
             '## Результат', '']
    if success:
        columns, rows = state['columns'], state['rows']
        if not rows:
            lines += ['Запрос вернул 0 строк. Это не ошибка SQL и не доказательство отсутствия данных вообще.']
        else:
            lines += ['| ' + ' | '.join(map(cell, columns)) + ' |',
                      '| ' + ' | '.join('---' for _ in columns) + ' |']
            lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
        lines += ['', f'Показано строк: {len(rows)}.']
        if state.get('truncated'):
            lines += ['**Результат обрезан по лимиту строк. Таблица выше — только часть ответа.**']
        lines += ['', 'Успешное выполнение проверяет синтаксис и доступ к данным, но не доказывает корректность бизнес-формулы.']
    else:
        lines += ['```text', state['error'].replace('```', '~~~'), '```']
    lines += ['', '## SQL', '', '[Сохранённый запрос](query.sql)', '', '```sql', sql.replace('```', '~~~'), '```', '', '## Попытки', '']
    for attempt in state.get('history', []):
        lines += [f"- Попытка {attempt['attempt']}: " + (cell(attempt['error']) if attempt.get('error') else 'успешно')]
    (folder / 'answer.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    # No DSN, API keys or model configuration are stored.
    (folder / 'trace.json').write_text(json.dumps({
        'question': state['question'], 'success': success,
        'attempts': state.get('history', []), 'error': state.get('error'),
        'truncated': state.get('truncated', False),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'artifact_dir': str(folder.resolve())}
