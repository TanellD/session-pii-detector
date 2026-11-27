"""Заморозить первый срез авторской разметки по исходным сценариям Андрея."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select(source, output):
    manifest = read(source / 'manifest.json')
    if manifest.get('synthetic_only') is not True:
        raise ValueError('Источник должен явно подтверждать синтетическое происхождение')
    docs = sorted(read(source / 'gold.json')['annotations'], key=lambda d: d['session_id'])[:250]
    if len(docs) != 250 or len({d['session_id'] for d in docs}) != 250:
        raise ValueError('Нужно 250 разных сессий')
    plans = {p['id']: p for p in read(source / 'scenario-plan.json')['scenarios']}
    assessments = {a['session_id']: a for a in read(source / 'gold-assessments.json')['checks']}
    records = []
    for doc in docs:
        plan, authored = plans[doc['session_id']], assessments[doc['session_id']]
        for e in doc['entities']:
            text = next(m['text'] for m in doc['messages'] if m['id'] == e['message_id'])
            if not 0 <= e['start'] < e['end'] <= len(text):
                raise ValueError('Неверные исходные границы: ' + doc['session_id'])
        expected = authored['author_expected_first_alert']
        first = None if expected is None else next(i for i, m in enumerate(doc['messages'], 1) if m['id'] == expected[0])
        records.append(dict(session_id=doc['session_id'], family=plan['family'],
            source_scenario_id=plan['source_scenario_id'], split=plan['split'], gold=doc,
            author_expected_first_alert=expected, expected_first_alert=first,
            author_assessment=authored['assessment']))
    result = dict(synthetic_only=True, source_version=manifest['version'],
        source_path=(source / 'gold.json').relative_to(ROOT).as_posix(),
        source_sha256=digest(source / 'gold.json'),
        source_hashes={name: digest(source / name) for name in ['manifest.json', 'gold.json',
            'scenario-plan.json', 'gold-assessments.json', 'author-alert-ledger.json']},
        frozen_revision_sha256=digest(ROOT / 'src/session_pii/adapter_revision.py'),
        selection='IDs sorted ascending; first 250 sessions; author gold copied unchanged; no external annotation outputs',
        evaluation_scope='Different authored wording-bank source based on Andrey original 16 scenarios; not independent human gold; overlapping scenario concepts remain possible; no rule tuning on this slice',
        annotation_source=manifest['annotation_source'], human_review_status=manifest['human_review_status'],
        counts=dict(sessions=len(docs), messages=sum(len(d['messages']) for d in docs),
            entities=sum(len(d['entities']) for d in docs), positive_sessions=sum(r['expected_first_alert'] is not None for r in records),
            types=dict(Counter(e['type'] for d in docs for e in d['entities'])),
            families=dict(Counter(r['family'] for r in records))), records=records)
    if output.exists():
        raise ValueError('Нужен новый файл снимка')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['counts'], ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/experiments/andrey-scenarios-500-2026-11-23')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/experiments/andrey-adapter-250-2026-11-25/snapshot.json')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'data'):
        parser.error('Снимок должен храниться вне git в data/')
    select(args.source.resolve(), args.output.resolve())
