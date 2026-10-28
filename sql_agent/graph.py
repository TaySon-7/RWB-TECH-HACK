from pathlib import Path
from typing import TypedDict

from langgraph.graph import START, END, StateGraph
from .artifacts import save_artifacts


class AgentState(TypedDict, total=False):
    question: str
    schema: str
    tables: set[str]
    sql: str
    assumptions: str
    attempts: int
    history: list[dict]
    error: str | None
    fatal: bool
    columns: list[str]
    rows: list
    truncated: bool
    artifact_dir: str


def build_graph(db, model, output: Path, max_attempts: int = 3):
    if not 1 <= max_attempts <= 10:
        raise ValueError('max_attempts must be between 1 and 10')

    def discover(state):
        try:
            schema, tables = db.schema()
            return {'schema': schema, 'tables': tables, 'attempts': 0, 'history': [], 'error': None, 'fatal': False}
        except Exception as exc:
            return {'error': f'Schema discovery: {type(exc).__name__}: {exc}', 'fatal': True}

    def generate(state):
        attempt = state['attempts'] + 1
        try:
            draft = model.generate(state['question'], state['schema'], state['history'])
            if not draft.sql.strip():
                return {'attempts': attempt, 'sql': '', 'assumptions': draft.assumptions,
                        'error': 'Недостаточно данных для запроса: ' + draft.assumptions, 'fatal': True}
            return {'attempts': attempt, 'sql': draft.sql, 'assumptions': draft.assumptions, 'error': None}
        except Exception as exc:
            return {'attempts': attempt, 'error': f'Model: {type(exc).__name__}: {exc}', 'fatal': True}

    def execute(state):
        try:
            result = db.execute(state['sql'], state['tables'])
            attempt = {'attempt': state['attempts'], 'sql': result['sql'], 'error': None}
            return {**result, 'error': None, 'history': [*state['history'], attempt]}
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            attempt = {'attempt': state['attempts'], 'sql': state['sql'], 'error': error}
            return {'error': error, 'history': [*state['history'], attempt]}

    def after_execute(state):
        if state.get('error') and state['attempts'] < max_attempts:
            return 'generate'
        return 'save'

    graph = StateGraph(AgentState)
    graph.add_node('schema', discover)
    graph.add_node('generate', generate)
    graph.add_node('execute', execute)
    graph.add_node('save', lambda state: save_artifacts(state, output))
    graph.add_edge(START, 'schema')
    graph.add_conditional_edges('schema', lambda state: 'save' if state.get('fatal') else 'generate')
    graph.add_conditional_edges('generate', lambda state: 'save' if state.get('fatal') else 'execute')
    graph.add_conditional_edges('execute', after_execute)
    graph.add_edge('save', END)
    return graph.compile()
