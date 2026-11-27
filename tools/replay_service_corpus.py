"""Прогон текстов синтетического корпуса через сервис без эталонных владельцев."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def replay(documents):
    from fastapi.testclient import TestClient
    from session_pii.app import create_app
    from session_pii.detectors import DetectorSettings

    client = TestClient(create_app(detector_settings=DetectorSettings(mode='regex')))
    sessions = []
    types = Counter()
    for index, document in enumerate(documents, 1):
        # Предсказателю передаются только тексты сообщений. Gold entities не читаются.
        state = client.post('/v1/sessions', json={'session_id': f'replay-{index}'}).json()
        messages = []
        for message in document['messages']:
            response = client.post('/v1/analyze', json=dict(
                session_id=state['session_id'], session_epoch=state['session_epoch'],
                expected_state_version=state['state_version'],
                message=dict(message_id=state['next_message_id'], role='user', text=message['text'])))
            if response.status_code != 200:
                raise ValueError(f"Service replay failed at session {index}, message {len(messages)+1}")
            state = response.json()
            types.update(e['type'] for e in state['entities'])
            messages.append(dict(source_message_id=message['id'], entities=state['entities'],
                                 suppressed=state['suppressed'], verdict=state['verdict']))
        sessions.append(dict(session_id=document['session_id'], messages=messages,
                             verdict=state['verdict']))
    return dict(mode='regex', linker='stub', gold_supplied=False,
                quality_metrics_computed=False, sessions=sessions,
                summary=dict(sessions=len(sessions), messages=sum(len(s['messages']) for s in sessions),
                             entity_types=dict(sorted(types.items())),
                             sessions_with_alert=sum(s['verdict']['session_alert'] for s in sessions)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() or not target.is_relative_to(ROOT / 'data'):
        parser.error('Укажите новый файл внутри data/; старый результат не перезаписывается')
    source = args.input.read_bytes()
    result = replay(json.loads(source))
    result['input_sha256'] = hashlib.sha256(source).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
