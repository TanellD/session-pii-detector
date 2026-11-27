"""Диагностика оставшихся ошибок контракта на замороженном срезе Андрея."""
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from session_pii.models import Span
from session_pii.normalization import overlaps, winner


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    base = ROOT / 'data/experiments/andrey-adapter-250-2026-11-25'
    summary = read(base / 'results/summary.json')
    docs = {r['session_id']: r['gold'] for r in read(base / 'snapshot.json')['records']}
    records = {r['text_sha256']: r for r in read(base / 'results/revised-recordings.json')}
    rows = []
    for error in summary['filtered']['revised']['errors']:
        message = docs[error['session_id']]['messages'][error['message_index'] - 1]
        spans = [Span.model_validate_json(json.dumps(s)) for s in records[hashlib.sha256(message['text'].encode()).hexdigest()]['spans']]
        pairs = [dict(types=sorted([a.type, b.type]),
            values=[message['text'][s.start:s.end] for s in [a, b]])
            for a, b in itertools.combinations(spans, 2) if overlaps(a, b) and winner(a, b) is None]
        rows.append(dict(**error, text=message['text'], unresolved_pairs=pairs))
    result = dict(errors=len(rows), reasons=dict(Counter(r['reason'] for r in rows)),
        unresolved_type_pairs=dict(Counter('+'.join(p['types']) for r in rows for p in r['unresolved_pairs'])), examples=rows[:5])
    (base / 'results/error-profile.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
