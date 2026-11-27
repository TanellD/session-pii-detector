"""Основания первого алерта: авторский момент и сохранённый ответ сервиса."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from session_pii.components import Fact
from session_pii.risk import RiskEvaluator, NAME_PARTS
from ownership import owner_ids


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reasons(doc, entities, first_alert, service_ids=()):
    texts = {m['id']: m['text'] for m in doc['messages']}
    positions = {m['id']: i for i, m in enumerate(doc['messages'], 1)}
    people, unknown = defaultdict(list), []
    for i, entity in enumerate(entities):
        mid = entity['message_id']
        if positions[mid] > first_alert:
            continue
        fact = Fact(entity['type'], texts[mid][entity['start']:entity['end']], entity['person_id'],
                    str(i), positions[mid], entity.get('service_contact', entity.get('id') in service_ids))
        owners = owner_ids(entity['person_id'])
        if owners:
            for owner in owners:
                people[owner].append(fact)
        else:
            unknown.append(fact)
    evaluator = RiskEvaluator()
    rule_set, categories, assessments = set(), set(), []
    for owner, is_unknown, facts in [(p, False, f) for p, f in people.items()] + [(None, True, unknown)]:
        assessment = evaluator.assess(tuple(facts), unknown=is_unknown)
        rule_set.update(assessment.rules)
        kinds = {f.type for f in facts if not f.service_contact}
        if assessment.rules:
            assessments.append(dict(owner=owner, unknown=is_unknown, score=assessment.risk_score,
                threshold=assessment.threshold, types=dict(Counter(f.type for f in facts if not f.service_contact)),
                rules=list(assessment.rules)))
        if 'red_rule' in assessment.rules:
            categories.add('red')
        if 'combination_rule' in assessment.rules:
            if {'phone', 'email'} <= kinds:
                categories.add('phone_email')
            if len(kinds & NAME_PARTS) >= 2 and kinds & {'phone', 'email', 'address'}:
                categories.add('name_contact')
    primary = next((r for r in ['red_rule', 'combination_rule', 'score_rule'] if r in rule_set), 'no_runtime_rule')
    if primary == 'score_rule':
        categories.add('score_only')
    return dict(primary=primary, rules=sorted(rule_set), categories=sorted(categories), assessments=assessments)


def profile(run):
    source = read(run / 'results/input-snapshot.json')
    state = read(run / 'results/manifest.json')
    records = {r['session_id']: r for r in source['records']}
    gold_rows = []
    for record in source['records']:
        first = record.get('expected_first_alert')
        if first is not None:
            gold_rows.append(dict(session_id=record['session_id'], family=record['family'],
                **reasons(record['gold'], record['gold']['entities'], first, record.get('gold_service_contacts', []))))
    latest = {r['session_id']: r for r in state['sessions']}
    completed = [r for r in latest.values() if r['status'] == 'complete']
    predicted_rows = []
    for row in completed:
        prediction = read(run / 'results' / row['path'])
        if prediction['first_alert'] is None:
            continue
        record = records[row['session_id']]
        predicted_rows.append(dict(session_id=row['session_id'], family=record['family'],
            gold_positive=record['expected_first_alert'] is not None,
            **reasons(record['gold'], prediction['entities'], prediction['first_alert'])))
    aggregate = lambda rows: dict(first_alerts=len(rows),
        primary=dict(Counter(r['primary'] for r in rows)),
        categories_inclusive=dict(Counter(c for r in rows for c in r['categories'])),
        rule_flags_inclusive=dict(Counter(c for r in rows for c in r['rules'])))
    return dict(selected_sessions=len(source['records']), completed_sessions=len(completed),
        gold_at_authored_trigger=aggregate(gold_rows), predicted_at_first_trigger=aggregate(predicted_rows),
        note='runtime policy applied diagnostically at authored gold trigger; gold labels unchanged; categories and flags can overlap',
        gold_cases=gold_rows, predicted_cases=predicted_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(ROOT / 'data'):
        parser.error('Нужен каталог внутри data/')
    result = profile(run)
    (run / 'alert-reasons.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if not k.endswith('_cases')}, ensure_ascii=False))
