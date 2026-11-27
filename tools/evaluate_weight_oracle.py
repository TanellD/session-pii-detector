"""Диагностика политики риска на авторских атрибутах, без NER и линкера."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT)]
from profile_alert_reasons import reasons
from evaluate_random_linker import alerts


def evaluate(snapshot):
    source = json.loads(snapshot.read_text(encoding='utf-8'))
    if source.get('synthetic_only') is not True:
        raise ValueError('Нужен явно синтетический корпус')
    cases = []
    for record in source['records']:
        doc = record['gold']
        first, steps = None, []
        for index in range(1, len(doc['messages']) + 1):
            assessed = reasons(doc, doc['entities'], index, record.get('gold_service_contacts', []))
            if first is None and assessed['primary'] != 'no_runtime_rule':
                first = index
            steps.append(dict(message_index=index, **assessed))
        cases.append(dict(session_id=record['session_id'], bucket=record['bucket'], family=record['family'],
            authored_first_alert=record['expected_first_alert'], predicted_first_alert=first, steps=steps))
    score = lambda rows: alerts([r['authored_first_alert'] for r in rows], [r['predicted_first_alert'] for r in rows])
    return dict(scope='gold spans and owners -> unchanged runtime risk; agreement with provisional author labels, not accepted privacy policy',
        label_policy_version=source.get('label_policy_version'), label_policy_status=source.get('label_policy_status'),
        input_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        runtime_policy_sha256=hashlib.sha256((ROOT / 'configs/runtime-risk-policy.json').read_bytes()).hexdigest(),
        sessions=len(cases), overall=score(cases), by_bucket={b: dict(sessions=sum(r['bucket']==b for r in cases),
            **score([r for r in cases if r['bucket']==b])) for b in sorted({r['bucket'] for r in cases})},
        first_trigger_rules=dict(Counter(next(s['primary'] for s in r['steps'] if s['message_index']==r['predicted_first_alert'])
                                        for r in cases if r['predicted_first_alert'] is not None)), cases=cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'data'):
        parser.error('Результат должен оставаться в data/')
    result = evaluate(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
