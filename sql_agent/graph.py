"""One tool-using dialogue agent with bounded execution and resumable questions."""
import json
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from .contracts import AgentResult, ClarificationRequest, FinalAnswer, QueryResult, QualityRequest, SchemaRequest, SQLRequest
from .prompts import SYSTEM, TABLE_MEANINGS
from .serialization import json_value
from .tools import TOOLS


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    question: str
    catalog: str
    tables: set[str]
    queries: dict
    history: list
    tool_calls: int
    sql_errors: int
    format_errors: int
    done: bool
    result: dict
    error: str | None
    artifact_dir: str


def build_graph(db, model, output: Path | None = None, max_attempts=3, *, checkpointer=None, max_tool_calls=12):
    """max_attempts includes the first SQL attempt; defaults to two repairs."""
    max_repairs = max_attempts - 1

    def prepare(state):
        try:
            catalog, tables = db.schema()
        except Exception:
            return fail(state, 'Не удалось прочитать схему БД. Проверьте доступность загруженного core.')
        context = {'dialect': getattr(db, 'dialect', 'postgres'), 'schema': json.loads(catalog), 'meanings': TABLE_MEANINGS}
        return {'catalog': json.dumps(context, ensure_ascii=False), 'tables': tables,
                'messages': [HumanMessage(content=state['question'])],
                'queries': {k: {**q, 'quality': {}} for k, q in state.get('queries', {}).items()},
                'history': [], 'tool_calls': 0,
                'sql_errors': 0, 'format_errors': 0, 'done': False, 'error': None}

    def fail(state, message):
        return {'done': True, 'error': message,
                'result': AgentResult(status='failed', summary=message,
                                      queries=list(state.get('queries', {}).values()),
                                      attempts=state.get('history', [])).model_dump(mode='json')}

    def call_model(state):
        if state.get('tool_calls', 0) >= max_tool_calls:
            return fail(state, 'Исчерпан бюджет инструментов. Успешные расчёты сохранены.')
        try:
            message = model.invoke([
                SystemMessage(content=SYSTEM + '\n\n<catalog>' + state['catalog'] + '</catalog>'),
                *state.get('messages', []),
            ], TOOLS)
            if not message.tool_calls:
                n = state.get('format_errors', 0) + 1
                if n > 2:
                    return fail(state, 'Модель не вернула структурированный итог.')
                return {'messages': [message, HumanMessage(content='Используй инструмент; итог верни через finish_answer.')],
                        'format_errors': n}
            return {'messages': [message]}
        except Exception as exc:
            return fail(state, f'Вызов модели не завершён: {type(exc).__name__}')

    def run_tools(state):
        queries = dict(state.get('queries', {}))
        history = list(state.get('history', []))
        sql_errors = state.get('sql_errors', 0)
        messages, done, result = [], False, None
        calls = state['messages'][-1].tool_calls
        for call in calls:
            name, args = call['name'], call['args']
            try:
                if len(calls) > 1 and name in {'ask_user', 'finish_answer'}:
                    raise ValueError('Уточнение и итог вызываются отдельно от других инструментов.')
                if name == 'get_schema':
                    request = SchemaRequest.model_validate(args)
                    payload = json.loads(state['catalog'])
                    if request.tables:
                        payload['requested_tables'] = request.tables
                elif name == 'query_sql':
                    request = SQLRequest.model_validate(args)
                    if sql_errors > max_repairs:
                        raise ValueError('Бюджет исправлений SQL исчерпан. Заверши по имеющимся данным.')
                    if history and history[-1].get('error') and history[-1].get('sql') == request.sql:
                        raise ValueError('Одинаковый ошибочный SQL нельзя повторять без исправления.')
                    try:
                        executed = db.execute(request.sql, state['tables'])
                    except Exception as exc:
                        sql_errors += 1
                        error = db.format_error(exc) if hasattr(db, 'format_error') else str(exc)
                        history.append({'tool': name, 'sql': request.sql, 'error': error})
                        payload = {'status': 'error', 'error': error,
                                   'repairs_remaining': max(0, max_repairs + 1 - sql_errors)}
                    else:
                        query_id = 'query_' + uuid4().hex[:12]
                        query = QueryResult(query_id=query_id, **json_value(executed)).model_dump(mode='json')
                        queries[query_id] = query
                        history.append({'tool': name, 'query_id': query_id, 'sql': request.sql})
                        payload = {**query, 'rows': query['rows'][:40],
                                   'preview_truncated': len(query['rows']) > 40 or query['truncated']}
                elif name == 'quality_report':
                    request = QualityRequest.model_validate(args)
                    if request.query_id not in queries:
                        raise ValueError('Неизвестный query_id. Сначала выполни query_sql.')
                    query = queries[request.query_id]
                    if not query.get('quality'):
                        query['quality'] = db.quality_report(query)
                    payload = {'query_id': request.query_id, **query['quality']}
                elif name == 'ask_user':
                    request = ClarificationRequest.model_validate(args)
                    answer = interrupt({'type': 'clarification', **request.model_dump()})
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError('Ответ должен содержать текст.')
                    payload = {'answer': answer.strip()}
                elif name == 'finish_answer':
                    answer = FinalAnswer.model_validate(args)
                    if not answer.query_ids and not answer.limitations:
                        raise ValueError('Ответ без выполненного query_id допустим только с явным объяснением недостающих данных в limitations.')
                    unknown = set(answer.query_ids) - queries.keys()
                    if unknown:
                        raise ValueError('Неизвестные query_ids: ' + ', '.join(sorted(unknown)))
                    missing = [q for q in answer.query_ids if not queries[q].get('quality')]
                    if missing:
                        raise ValueError('Сначала вызови quality_report для: ' + ', '.join(missing))
                    selected = [queries[q] for q in answer.query_ids]
                    limitations = list(answer.limitations)
                    for q in selected:
                        for warning in q['quality'].get('limitations', []):
                            if warning not in limitations:
                                limitations.append(warning)
                        if q['truncated']:
                            limitations.append(f"{q['query_id']}: показаны первые {len(q['rows'])} строк; полный агрегат считается в БД.")
                    partial = bool(limitations) or any(q['quality'].get('affects_result') for q in selected)
                    result = AgentResult(status='partial' if partial else 'completed', summary=answer.summary,
                                         queries=selected, limitations=limitations, attempts=history).model_dump(mode='json')
                    payload, done = {'status': 'accepted'}, True
                else:
                    raise ValueError('Неизвестный инструмент.')
            except (ValueError, KeyError) as exc:
                payload = {'status': 'error', 'error': str(exc)}
            messages.append(ToolMessage(content=json.dumps(json_value(payload), ensure_ascii=False),
                                        tool_call_id=call['id'], name=name))
        return {'messages': messages, 'queries': queries, 'history': history,
                'sql_errors': sql_errors, 'tool_calls': state.get('tool_calls', 0) + len(calls),
                'done': done, **({'result': result} if result else {})}

    def save(state):
        if output is None:
            return {}
        from .artifacts import save_agent_result
        return save_agent_result(state, output)

    graph = StateGraph(AgentState)
    graph.add_node('context', prepare)
    graph.add_node('agent', call_model)
    graph.add_node('tools', run_tools)
    graph.add_node('save', save)
    graph.add_edge(START, 'context')
    graph.add_conditional_edges('context', lambda s: 'save' if s.get('done') else 'agent')
    graph.add_conditional_edges('agent', lambda s: 'save' if s.get('done') else ('tools' if getattr(s['messages'][-1], 'tool_calls', None) else 'agent'))
    graph.add_conditional_edges('tools', lambda s: 'save' if s.get('done') else 'agent')
    graph.add_edge('save', END)
    return graph.compile(checkpointer=checkpointer)
