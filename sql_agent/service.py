"""Shared dialogue service: SQLite checkpoints, bounded workers, resume by thread ID."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .contracts import AgentResult
from .graph import build_graph
from .infra.llm import AgentModel


def database(settings):
    if settings.backend == 'clickhouse':
        from .infra.clickhouse import ClickHouseDatabase
        return ClickHouseDatabase(settings.clickhouse_url, settings.clickhouse_user,
                                  settings.clickhouse_password.get_secret_value(), settings.timeout_ms, settings.max_rows)
    from .infra.postgres import Database
    return Database(settings.database_url, settings.timeout_ms, settings.max_rows)


class AgentService:
    def __init__(self, settings, *, db=None, model=None):
        self.settings, self.db, self.model = settings, db, model
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = settings.runtime_dir / 'checkpoints.sqlite'
        self.conn = sqlite3.connect(checkpoint_path, timeout=30, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL)')
        self.conn.commit()
        self.saver = SqliteSaver(self.conn)
        self.owners = sqlite3.connect(checkpoint_path, timeout=30, check_same_thread=False)
        self.graph = None
        self.lock = threading.RLock()
        self.jobs = {}
        self.slots = threading.BoundedSemaphore(2)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sql-agent')

    def _graph(self):
        with self.lock:
            if self.graph is None:
                self.db = self.db or database(self.settings)
                self.model = self.model or AgentModel(self.settings)
                self.graph = build_graph(self.db, self.model, self.settings.runtime_dir / 'answers',
                                         self.settings.max_sql_repairs + 1, checkpointer=self.saver,
                                         max_tool_calls=self.settings.max_tool_calls)
            return self.graph

    def config(self, conversation_id):
        return {'configurable': {'thread_id': conversation_id},
                'recursion_limit': 2 * self.settings.max_tool_calls + 15}

    def _authorize(self, conversation_id, owner):
        UUID(conversation_id)
        row = self.owners.execute('SELECT owner FROM conversations WHERE id=?', (conversation_id,)).fetchone()
        if not row or row[0] != owner:
            raise KeyError('Диалог не найден.')

    def submit(self, text, owner='local', conversation_id=None, *, resume=False):
        graph = self._graph()
        with self.lock:
            if conversation_id:
                self._authorize(conversation_id, owner)
            else:
                if resume:
                    raise ValueError('Для продолжения нужен ID диалога.')
                conversation_id = str(uuid4())
                self.owners.execute('INSERT INTO conversations VALUES (?,?)', (conversation_id, owner))
                self.owners.commit()
            previous = self.jobs.get(conversation_id, {})
            if previous.get('status') == 'running':
                raise ValueError('В этом диалоге уже выполняется расчёт.')
            snapshot = graph.get_state(self.config(conversation_id))
            pending = [i for task in snapshot.tasks for i in task.interrupts]
            if resume != bool(pending):
                raise ValueError('Сначала ответьте на уточнение.' if pending else 'В диалоге нет ожидающего уточнения.')
            if not self.slots.acquire(blocking=False):
                raise ValueError('Два расчёта уже выполняются. Повторите через несколько секунд.')
            self.jobs[conversation_id] = {'status': 'running', 'stage': 'Читаю схему и готовлю расчёт', 'events': []}
            command = Command(resume={pending[0].id: text}) if resume else {'question': text}
            self.pool.submit(self._run, conversation_id, command)
            return conversation_id

    def _run(self, conversation_id, command):
        labels = {'context': 'Схема прочитана', 'agent': 'Агент выбирает следующий шаг',
                  'tools': 'Проверяю результат инструмента', 'save': 'Сохраняю SQL и доказательства'}
        try:
            for update in self.graph.stream(command, self.config(conversation_id), stream_mode='updates'):
                with self.lock:
                    for node, payload in update.items():
                        job = self.jobs[conversation_id]
                        job['stage'] = labels.get(node, 'Нужно уточнение')
                        for entry in (payload or {}).get('history', []) if isinstance(payload, dict) else []:
                            event = {'tool': entry['tool'], 'status': 'error' if entry.get('error') else 'ok'}
                            if event not in job['events'][-1:]:
                                job['events'].append(event)
            with self.lock:
                self.jobs[conversation_id]['status'] = 'idle'
        except Exception as exc:
            with self.lock:
                self.jobs[conversation_id] = {'status': 'failed', 'result': AgentResult(
                    status='failed', summary=f'Расчёт прерван ({type(exc).__name__}). Можно повторить вопрос.').model_dump()}
        finally:
            self.slots.release()

    def read(self, conversation_id, owner='local'):
        with self.lock:
            self._authorize(conversation_id, owner)
            job = dict(self.jobs.get(conversation_id, {}))
            if job.get('status') in {'running', 'failed'}:
                return {'conversation_id': conversation_id, **job}
            snapshot = self._graph().get_state(self.config(conversation_id))
            pending = [i for task in snapshot.tasks for i in task.interrupts]
            if pending:
                return {'conversation_id': conversation_id, 'status': 'waiting',
                        'clarification': pending[0].value, 'events': job.get('events', [])}
            if snapshot.values.get('result'):
                return {'conversation_id': conversation_id, 'status': 'done',
                        'result': snapshot.values['result'], 'events': job.get('events', [])}
            return {'conversation_id': conversation_id, 'status': 'failed',
                    'result': {'status': 'failed', 'summary': 'Сохранённый расчёт не завершился. Повторите вопрос.'}}

    def close(self):
        self.pool.shutdown(wait=True)
        self.conn.close()
        self.owners.close()
