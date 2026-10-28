# Capibara: диалоговый SQL-агент

Один агент на LangGraph + ChatOpenAI. Он получает актуальную схему БД и правила
метрик, сам выбирает инструменты, читает результаты и отвечает по ним.
UI находится внутри существующей Grafana и отдельно на `/agent/`.

## Подключение LLM

Из корня репозитория заполните **`.env.agent`**. Файл уже подготовлен локально;
для нового окружения: `cp sql_agent/.env.example .env.agent`.
Обязательны `LLM_MODEL` и `LLM_API_KEY`. `LLM_BASE_URL` — URL OpenAI-совместимого
endpoint с `/v1`; пустое значение использует стандартный endpoint SDK.
Модель должна поддерживать вызовы инструментов. По умолчанию используется
Chat Completions; `LLM_USE_RESPONSES_API=true` включается для endpoint с Responses.
Никакие реальные ключи и endpoint с другого сервера в проект не включены.

```bash
python3 -m venv sql_agent/.venv
sql_agent/.venv/bin/pip install -r sql_agent/requirements.txt
# PostgreSQL локально, localhost:5433; интерактивные уточнения прямо в CLI:
sql_agent/.venv/bin/python -m sql_agent "Какая выручка по месяцам?" --json
# Отдельный интерфейс с той же логикой:
sql_agent/.venv/bin/python -m uvicorn sql_agent.web:app --host 127.0.0.1 --port 8008
```

Для ClickHouse/Grafana: `bash scripts/analytics.sh up`.
После изменения LLM-настроек:

```bash
docker compose --env-file .env.analytics -f docker-compose.analytics.yml up -d --force-recreate agent
```
Затем откройте http://localhost:3000/d/capibara-commerce и войдите в Grafana.
Стартовый вопрос об общей выручке выполняется один раз в новом диалоге;
перезагрузка дашборда восстанавливает локально сохранённый ответ.

## Цикл агента

```mermaid
flowchart LR
    Q[Вопрос и история] --> S[Актуальная схема и правила метрик]
    S --> A[LLM выбирает инструмент]
    A --> T[SELECT: валидация и выполнение]
    T -->|Результат или SQL-ошибка| A
    A --> D[Детерминированный аудит query_id]
    D --> A
    A --> U[Уточнение: interrupt]
    U -->|Ответ: resume того же thread_id| A
    A --> F[Итог: таблицы, SQL, ограничения]
```

Инструменты: `get_schema`, `query_sql`, `quality_report`, `ask_user`, `finish_answer`.
Итог может ссылаться только на реально выполненные `query_id`; аудит обязателен.
Модель получает до 40 строк каждого результата. UI показывает до 200 по умолчанию,
помечает обрезку и позволяет открыть тот же SQL в Grafana Explore. Суммы считаются
в БД до ограничения вывода. Подключение LLM передаёт провайдеру схему, историю,
SQL и ограниченные результаты — это часть работы диалогового агента.

Repair: максимум две коррекции после первой SQL-ошибки, максимум 12 вызовов
инструментов за ход. Модель видит сообщение БД и исправляет запрос. Корректный
пустой результат не вызывает repair. Успешный SELECT не доказывает правильность
бизнес-формулы: её видно в SQL и кратком объяснении.

## Кто отвечает за качество

Загрузчик, а не LLM, определяет пригодность записи. Правила относятся к типам,
ключам, ссылкам и версиям, а не к конкретным ID/контрольным ответам.
В PostgreSQL `audit.source_record` содержит оригиналы и решения,
`audit.issue` — причины. В ClickHouse `ops.rejected_rows` сохраняет исходные
непригодные записи, `ops.load_manifests` — файлы, хеши и счётчики партии.
Подозрительные факты с допустимыми типами остаются в core и отмечаются в `dq_issue`.

Отчёт различает допущенные, отклонённые записи, дубли и сохранённые предупреждения.
Для поддержанных SELECT/CTE воспроизводится FROM/JOIN/WHERE до GROUP/HAVING/TOP:
это охват **входных** записей, не универсальное происхождение каждой выходной ячейки.
На сложных формах SQL показывается явное ограничение и отчёт источников.
Запись без seller_id нельзя приписать выбранному продавцу: такие случаи
показываются отдельно как неизвестная принадлежность, не выдуманная потеря выручки.
Общие предупреждения всего core также подписаны отдельно от охвата запроса.

## Структура

- `graph.py`, `tools.py`, `contracts.py`, `prompts.py` — решения агента и контракты.
- `infra/` — LLM, PostgreSQL/ClickHouse, проверка SQL.
- `quality.py` — аудит выполненного запроса обычным кодом.
- `service.py` — диалоги, SQLite checkpoints, два ограниченных исполнителя.
- `web.py`, `static/` — небольшой чат без дополнительного frontend-фреймворка.
- `artifacts.py` — результат JSON, SQL и читаемый отчёт, без конфигурации/ключей.

SQL допускает один SELECT/CTE к реальным core/mart таблицам и аналитические функции.
Для PostgreSQL дополнительно включён read-only и timeout; ClickHouse использует
отдельного `agent_reader` с SELECT. UI за общим gateway пользуется существующей
авторизацией Grafana; пользователь не получает ключ модели или пароль БД.
Диалоги и артефакты сохраняются в локальном каталоге или Docker volume agent_data.

## Проверка

```bash
sql_agent/.venv/bin/python -m pytest sql_agent/tests -q
TEST_DATABASE_URL=postgresql://hack:hack@localhost:5433/marketplace \
  sql_agent/.venv/bin/python -m pytest sql_agent/tests -q
```

Тесты проверяют инструменты, SQL repair, interrupt/resume, запрет выдуманных
query_id, обязательный аудит, таймауты/лимиты, доступ к диалогам и пять контрольных
SQL на реальной БД. Ответы LLM подменены: качество генерации естественного языка
нужно проверить после подключения выбранной вами модели.

Ссылки: [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
[ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai),
[Grafana Explore URL](https://grafana.com/docs/grafana/latest/visualizations/explore/get-started-with-explore/).
