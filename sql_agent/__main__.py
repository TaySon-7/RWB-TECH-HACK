import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from .db import Database
from .graph import build_graph
from .llm import SQLModel


def main():
    load_dotenv(Path(__file__).parent / '.env')
    parser = argparse.ArgumentParser(description='LangGraph text-to-SQL agent for marketplace core')
    parser.add_argument('question', help='Question in natural language')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'artifacts')
    parser.add_argument('--max-attempts', type=int, default=int(os.getenv('SQL_AGENT_MAX_ATTEMPTS', '3')))
    args = parser.parse_args()
    if not args.question.strip():
        parser.error('question cannot be empty')
    if not os.getenv('OPENAI_API_KEY') or not os.getenv('OPENAI_MODEL'):
        parser.error('Set OPENAI_API_KEY and OPENAI_MODEL in sql_agent/.env or environment')
    max_rows = int(os.getenv('SQL_AGENT_MAX_ROWS', '1000'))
    timeout = int(os.getenv('SQL_AGENT_TIMEOUT_MS', '15000'))
    if max_rows <= 0 or timeout <= 0 or not 1 <= args.max_attempts <= 10:
        parser.error('Positive row/timeout limits and 1..10 attempts are required')
    db = Database(os.getenv('DATABASE_URL', 'postgresql://hack:hack@localhost:5432/marketplace'), timeout, max_rows)
    model = SQLModel(os.environ['OPENAI_MODEL'])
    graph = build_graph(db, model, args.output, args.max_attempts)
    state = graph.invoke({'question': args.question}, config={'recursion_limit': 2 * args.max_attempts + 10})
    print('Результат: ' + state['artifact_dir'] + '/answer.md')
    print('SQL: ' + state['artifact_dir'] + '/query.sql')
    return 1 if state.get('error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
