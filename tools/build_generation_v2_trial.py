"""Материализация малой авторской партии v2; без train/test и оценки сервиса."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from annotation_contract import assess
from annotation_calculator import Calculator
from build_difficulty_pilot import materialize, verify_labels, render
from build_medium_scenario_examples import profile, FIRST, LAST


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def profiles(rng):
    """Every field draws separately; no index, level or outcome is supplied."""
    result = {}
    names = rng.sample([(f, l) for f in FIRST for l in LAST], 4)
    for owner, (first, last) in zip(('a', 'b', 'c', 's'), names):
        # Existing helpers guarantee document checksums. Their shared n is
        # deliberately discarded: each field uses its own independent draw.
        item = {key: profile(rng.randrange(100, 999))[key]
                for key in profile(100)}
        item.update(first=first, last=last,
                    phone=f'+7 (000) {rng.randrange(100, 999):03d}-{rng.randrange(10000):04d}',
                    phone_new=f'+7 (000) {rng.randrange(100, 999):03d}-{rng.randrange(10000):04d}',
                    email=f'contact{rng.randrange(100000, 999999)}@example.invalid')
        result[owner] = item
    for pair in ('ab', 'ac', 'bc'):
        result[pair] = dict(result[pair[0]])
    result['u'] = dict(result['b'])
    return result


def audit(output):
    snapshot = read(output/'snapshot.json')
    calculator = Calculator()
    counts, outcomes, lengths, instructions = Counter(), Counter(), {}, []
    saved = read(output/'calculator-assessments.json')
    dialogues = set()
    phone_codes = {'positive': [], 'negative': []}
    for row in snapshot['records']:
        verify_labels(row)
        result = assess(row['gold'], calculator)
        assert result == saved[row['session_id']], 'Prefix replay differs'
        texts = tuple(m['text'] for m in row['gold']['messages'])
        assert texts not in dialogues, 'Duplicate session'
        dialogues.add(texts)
        assert not any('[[' in t or '\ufffd' in t for t in texts)
        if any(re.search(r'не (?:объединяйте|назначайте|копируйте)|без владельца', t, re.I) for t in texts):
            instructions.append(row['session_id'])
        counts.update(e['type'] for e in row['gold']['entities'])
        outcomes['positive' if result['first_alert'] else 'negative_uncertain' if result['messages'][-1]['annotation_uncertain'] else 'negative_certain'] += 1
        group = 'positive' if result['first_alert'] else 'negative'
        for text in texts:
            phone_codes[group].extend(int(v) for v in re.findall(r'\+7 \(000\) (\d{3})-', text))
        lengths.setdefault(row['difficulty'], []).append(len(texts))
    assert read(output/'sessions.json') == [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in snapshot['records']]
    return dict(structural_status='passed', sessions=len(dialogues), entity_counts=dict(counts),
                outcomes=dict(outcomes), lengths=lengths, owner_instruction_flags=instructions,
                value_assignment='Separate RNG draws per field; no outcome/difficulty/split input',
                shortcut_limit='Process check only; small sample cannot establish statistical independence.',
                phone_code_ranges={key: [min(values), max(values)] if values else None
                                   for key, values in phone_codes.items()},
                split_coverage='Not applicable: development_only', benchmark_ready=False)


def build(plan_path, output):
    if output.exists():
        raise ValueError('Use a new directory')
    plan = read(plan_path)
    assert plan['generation_policy'] == 'synthetic-session-generation-v2'
    assert len(plan['cases']) == plan['session_count']
    rng = random.Random(plan['value_seed'])
    # Allocate all profiles before processing cases; labels never influence draws.
    allocated = [profiles(rng) for _ in plan['cases']]
    records, assessments = [], {}
    calculator = Calculator()
    for index, (case, values) in enumerate(zip(plan['cases'], allocated), 1):
        messages, entities, service_ids, controls = materialize(case['messages'], values)
        sid = f'v2trial-{index:03d}'
        doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages, entities=entities,
                   case_flags=dict(is_example=False, annotation_uncertain=False, clarification_reason=None))
        result = assess(doc, calculator)
        doc['case_flags'] = result['case_flags']
        result = assess(doc, calculator)
        record = dict(session_id=sid, family=case['family'], title=case['title'],
                      difficulty=case['difficulty'], difficulty_reason=case['evidence'], split='development',
                      gold=doc, gold_service_contacts=service_ids, negative_controls=controls,
                      expected_first_alert=result['first_alert']['message_index'] if result['first_alert'] else None,
                      final_uncertain=result['messages'][-1]['annotation_uncertain'],
                      source_paragraph_layout=[[i] for i in range(1, len(messages)+1)])
        verify_labels(record)
        records.append(record)
        assessments[sid] = result
    output.mkdir(parents=True)
    write(output/'generation-plan.json', plan)
    manifest = dict(version='generation-v2-trial-18-v1', generation_policy=plan['generation_policy'],
                    synthetic_only=True, benchmark_ready=False, split_mode='development_only',
                    annotation_source='assistant_authored_spans_and_owners_plus_calculator',
                    independent_review=False, service_weights_used_for_gold=False,
                    annotation_policy_version=calculator.policy['version'],
                    plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                    code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    dependency_sha256={name: hashlib.sha256((ROOT/'tools'/name).read_bytes()).hexdigest()
                                       for name in ['build_difficulty_pilot.py', 'build_medium_scenario_examples.py']},
                    reference_sha256=hashlib.sha256(json.dumps(calculator.policy, sort_keys=True).encode()).hexdigest())
    write(output/'manifest.json', manifest)
    write(output/'snapshot.json', dict(**manifest, records=records))
    write(output/'gold.json', [r['gold'] for r in records])
    write(output/'sessions.json', [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in records])
    write(output/'calculator-assessments.json', assessments)
    (output/'examples-for-review.md').write_text(render(records, assessments, 'Пробная партия генерации v2'), encoding='utf-8')
    return audit(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must be in data/')
    report = audit(args.output) if args.verify_only else build(args.plan, args.output)
    write(args.output/'validation-report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
