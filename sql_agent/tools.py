"""Model-visible schemas. Execution stays in graph.py and database adapters."""
from .contracts import ClarificationRequest, FinalAnswer, QualityRequest, SchemaRequest, SQLRequest


def schema(name, description, model):
    return {'type': 'function', 'function': {
        'name': name, 'description': description, 'parameters': model.model_json_schema(),
    }}


TOOLS = [
    schema('get_schema', 'Реальная структура и ключи таблиц, без исходных строк.', SchemaRequest),
    schema('query_sql', 'Выполнить один SELECT и получить query_id, колонки и реальные строки.', SQLRequest),
    schema('quality_report', 'Детерминированный отчёт о качестве и происхождении выполненного query_id.', QualityRequest),
    schema('ask_user', 'Приостановить расчёт и получить решение существенной неоднозначности.', ClarificationRequest),
    schema('finish_answer', 'Завершить ответ со ссылками на реальные query_id и ограничениями.', FinalAnswer),
]
