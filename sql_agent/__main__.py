import argparse
import json
import time
from .service import AgentService
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description='Tool-using marketplace SQL agent')
    parser.add_argument('question', help='Question in natural language')
    parser.add_argument('--json', action='store_true', help='Print the complete verified result')
    args = parser.parse_args()
    if not args.question.strip():
        parser.error('question cannot be empty')
    service = AgentService(Settings())
    try:
        conversation_id = service.submit(args.question)
        while True:
            state = service.read(conversation_id)
            if state['status'] == 'running':
                time.sleep(.3)
                continue
            if state['status'] == 'waiting':
                prompt = state['clarification']
                print(prompt['question'])
                print(' / '.join(prompt['options']))
                service.submit(input('> '), conversation_id=conversation_id, resume=True)
                continue
            result = state['result']
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result['summary'])
            if not args.json and result.get('artifact_dir'):
                print('SQL и доказательства: ' + result['artifact_dir'])
            return int(result['status'] == 'failed')
    finally:
        service.close()


if __name__ == '__main__':
    raise SystemExit(main())
