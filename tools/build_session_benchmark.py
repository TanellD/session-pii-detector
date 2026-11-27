"""Синтетический train/validation/test по числу людей и длине; v3."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from annotation_calculator import Calculator
from annotation_contract import assess, validate
from annotation_address_enrichment import build_enrichment
from build_medium_scenario_examples import TYPES as BASE_TYPES, profile, apartment_confirmation
from build_difficulty_pilot import verify_labels, render
from group_session_benchmark import group_key
from session_benchmark_blueprints import SPLITS, CONTEXTS, NEUTRAL, MODES, story

VERSION = 'session-benchmark-2400-v5'
COUNTS = dict(train=180, validation=60, test=60)
TYPES = dict(BASE_TYPES, person='person', passport='passport')
SLOT = re.compile(r'\[\[(a|b|c|ab|u|s):([a-z_]+)\]\]')


def hash_value(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')


def name_pool(rng):
    roots = ['Макет', 'Тест', 'Проб', 'Образ', 'Демо', 'Шаблон', 'Эскиз', 'Муляж']
    first = [r+e for r in roots for e in ['имир', 'ослав', 'омир', 'одар', 'олюб', 'ислав', 'омысл', 'ивей',
                                        'имира', 'ослава', 'омира', 'одара', 'олюба', 'ислава', 'омысла', 'ивея']]
    last = [r+n+e for r in roots for n in ['олес', 'оград', 'ополь', 'олуг', 'ореч', 'осад', 'одол', 'огор'] for e in ['ов', 'ин', 'ский', 'ев']]
    names = [(f, (l[:-4]+'ская' if l.endswith('ский') else l+'а') if f.endswith(('а', 'я')) else l)
             for f in first for l in last]
    rng.shuffle(names)
    return names


def independent_profiles(rng, names):
    profiles, used = {}, set()
    for owner in 'abcs':
        postponed = []
        while True:
            first, last = names.pop()
            if first not in used:
                break
            postponed.append((first, last))
        names[:0] = postponed
        used.add(first)
        docs = {key: profile(rng.randrange(1000, 900000))[key] for key in ('inn', 'snils', 'card')}
        age = rng.randrange(22, 65)
        unit = 'год' if age % 10 == 1 and age % 100 != 11 else 'года' if age % 10 in (2, 3, 4) and age % 100 not in (12, 13, 14) else 'лет'
        female = first.endswith(('а', 'я'))
        patronymics = ['Макетовна', 'Тестовна', 'Образовна', 'Пробомировна'] if female else ['Макетович', 'Тестович', 'Образович', 'Пробомирович']
        profiles[owner] = dict(first=first, last=last, patronymic=rng.choice(patronymics),
            phone=f'+7 (000) {rng.randrange(100, 1000):03d}-{rng.randrange(100):02d}-{rng.randrange(100):02d}',
            email=f'mail{rng.randrange(10**10, 10**11)}@example.invalid',
            address=f'г. {rng.choice(["Пробоград", "Макетск", "Демогорск", "Тестоград"])}, ул. {rng.choice(["Макетная", "Пробная", "Эскизная"])}, д. {rng.randrange(1, 200)}, кв. {rng.randrange(1, 200)}',
            occupation=rng.choice(['бухгалтер', 'редактор', 'дизайнер', 'инженер']),
            organization=f'ООО «{rng.choice(["Макетный контур", "Пробный проект", "Эскизный цех"])} {rng.randrange(10000, 99999)}»',
            geography=rng.choice(['Пробоград', 'Макетск', 'Демогорск', 'Тестоград']),
            age=f'{age} {unit}', sex='женский' if female else 'мужской', passport=f'00 00 {rng.randrange(1000000):06d}',
            person=f'Аль-{first} ибн {last}', bad_phone=rng.choice(['abc12', '+7 (000) 12', '123-45']),
            masked_card='0000 **** **** '+str(rng.randrange(1000, 10000)), **docs)
    profiles['ab'] = dict(profiles['a'])
    profiles['u'] = dict(profiles['b'])
    return profiles


def materialize(lines, profiles):
    messages, entities, services, controls = [], [], [], []
    for index, template in enumerate(lines, 1):
        text, offset = '', 0
        for match in SLOT.finditer(template):
            text += template[offset:match.start()]
            owner, field = match.groups()
            value = profiles[owner][field]
            start = len(text)
            text += value
            if field in TYPES:
                entity = dict(id=f'e{len(entities)+1}', message_id=f'm{index}', start=start, end=len(text),
                              type=TYPES[field], person_id=None if owner in ('u', 's') else ['a', 'b'] if owner == 'ab' else owner)
                entities.append(entity)
                if owner == 's':
                    services.append(entity['id'])
            else:
                controls.append(dict(message_id=f'm{index}', start=start, end=len(text), kind=field))
            offset = match.end()
        text += template[offset:]
        messages.append(dict(id=f'm{index}', text=text))
    return messages, entities, services, controls


def layout(lines, length, split, rng):
    lines = list(lines)
    extras = list(NEUTRAL[split])
    needed = max(0, length-len(lines))
    # Keep the troubleshooting chain coherent; never insert unrelated queries
    # before the speaker introduces themselves.
    if needed:
        position = rng.randrange(2, len(lines)+1) if len(lines) > 2 else len(lines)
        lines[position:position] = extras[:needed]
    while len(lines) > length:
        position = rng.randrange(len(lines)-1)
        lines[position:position+2] = [lines[position]+' '+lines[position+1]]
    return lines


def linker_rows(records):
    """Supervised prefix examples: previous gold only, never future gold."""
    for row in records:
        known, past, unowned = {}, [], []
        doc = row['gold']
        services = set(row['gold_service_contacts'])
        for index, message in enumerate(doc['messages'], 1):
            mentions = [e for e in doc['entities'] if e['message_id'] == message['id']]
            candidates = [dict(candidate_id=f'c{i}', type=e['type'], start=e['start'], end=e['end'],
                               value=message['text'][e['start']:e['end']]) for i, e in enumerate(mentions, 1)]
            request = dict(message=dict(message_id=index, text=message['text']), previous_messages=list(past),
                           candidates=candidates, known_people=[dict(person_id=p, facts=list(f)) for p, f in known.items()],
                           unowned_facts=list(unowned))
            new, assignments = {}, []
            for e, candidate in zip(mentions, candidates):
                owners = e['person_id'] if isinstance(e['person_id'], list) else [] if e['person_id'] is None else [e['person_id']]
                for p in owners:
                    if p not in known:
                        new.setdefault(p, f'new:{len(new)+1}')
                assignments.append(dict(candidate_id=candidate['candidate_id'], owners=[p if p in known else new[p] for p in owners],
                                        service_contact=e['id'] in services))
            yield dict(session_id=doc['session_id'], message_id=message['id'],
                       mode='supervised_gold_prefix_and_candidates', request=request, target=dict(assignments=assignments))
            for e, candidate in zip(mentions, candidates):
                fact = dict(type=candidate['type'], value=candidate['value'])
                owners = e['person_id'] if isinstance(e['person_id'], list) else [] if e['person_id'] is None else [e['person_id']]
                if not owners and e['id'] not in services:
                    unowned.append(fact)
                for p in owners:
                    known.setdefault(p, []).append(fact)
            past.append(dict(message_id=index, text=message['text']))


def audit(output):
    snapshot = read(output/'snapshot.json')
    records, calculator = snapshot['records'], Calculator()
    results = read(output/'calculator-assessments.json')
    enrichments = read(output/'address-enrichment.json')
    checks = ['schema_spans_owners_checksums', 'all_prefix_calculator_replay', 'exact_cell_sizes',
              'no_family_profile_contact_leakage', 'no_duplicate_normalized_dialogue_across_splits', 'input_exports_equal_gold_texts']
    families, full_names, contacts, dialogues = {}, {}, {}, {}
    summary = {}
    for row in records:
        verify_labels(row)
        assert group_key(row['gold']) == (row['people_count'], row['length_group'])
        result = assess(row['gold'], calculator, address_enrichment=enrichments[row['session_id']])
        assert result == results[row['session_id']], row['session_id']
        assert (result['first_alert']['message_index'] if result['first_alert'] else None) == row['expected_first_alert']
        if not row['people_count']:
            assert result['first_alert'] is None
        for registry, keys in [(families, [row['family']]), (full_names, row['profile_keys'])]:
            for key in keys:
                assert registry.setdefault(key, row['split']) == row['split'], ('group leak', key)
        texts = validate(row['gold'])
        for e in row['gold']['entities']:
            if e['type'] in ('phone', 'email'):
                key = (e['type'], texts[e['message_id']][e['start']:e['end']])
                assert contacts.setdefault(key, row['split']) == row['split'], ('contact leak', key)
        parts = []
        for message in row['gold']['messages']:
            text = message['text']
            for e in sorted([e for e in row['gold']['entities'] if e['message_id'] == message['id']], key=lambda e:e['start'], reverse=True):
                text = text[:e['start']]+'<'+e['type']+'>'+text[e['end']:]
            parts.append(text)
        key = hash_value(parts)
        assert dialogues.setdefault(key, row['split']) == row['split'], 'Normalized cross-split duplicate'
    for split in SPLITS:
        subset = [r for r in records if r['split'] == split]
        inputs = [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in subset]
        assert read(output/split/'sessions.json') == inputs
        assert read(output/split/'gold.json') == [r['gold'] for r in subset]
        cell_counts = Counter(f'{r["people_count"]}-{r["length_group"]}' for r in subset)
        assert len(cell_counts) == 8 and set(cell_counts.values()) == {COUNTS[split]}
        types = Counter(e['type'] for r in subset for e in r['gold']['entities'])
        taxonomy = set(read(ROOT/'configs/annotation-classes.json')['classes'])
        assert not taxonomy-set(types), ('Missing classes', split, taxonomy-set(types))
        assert min(types.values()) >= 24, ('Insufficient class coverage', split, dict(types))
        outcomes = Counter('positive' if r['expected_first_alert'] is not None else 'negative_uncertain' if r['final_uncertain'] else 'negative_certain' for r in subset)
        reasons = Counter(results[r['session_id']]['first_alert']['reason'] for r in subset if results[r['session_id']]['first_alert'])
        summary[split] = dict(sessions=len(subset), messages=sum(len(r['gold']['messages']) for r in subset),
                              entities=sum(types.values()), type_counts=dict(types), groups=dict(cell_counts),
                              outcomes=dict(outcomes), alert_reasons=dict(reasons), scenario_families=len({r['family'] for r in subset}))
        for people in (1, 2, 3):
            for bucket in ('short', 'long'):
                cell = [r for r in subset if r['people_count'] == people and r['length_group'] == bucket]
                assert any(r['expected_first_alert'] is not None for r in cell)
                assert any(r['expected_first_alert'] is None for r in cell)
    return dict(status='passed', checks=checks, splits=summary, benchmark_ready=False,
                limitations=['Authored compositional templates, not independent natural dialogues.',
                             'Exact duplicate checks do not establish semantic independence.',
                             'Review and readiness tracked separately; test must not tune service.'])


def build(output):
    if output.exists():
        raise ValueError('Use a new directory')
    output.mkdir(parents=True)
    plan = dict(version=VERSION, generation_policy='synthetic-session-generation-v3', session_count=2400,
                split_sizes=dict(train=1440, validation=480, test=480), counts_per_cell=COUNTS,
                people_counts=[0, 1, 2, 3], short=[2, 5], long=[6, 8],
                generation='authored_compositional_templates_plus_independent_synthetic_values',
                values_seed=89147261, layout_seed=27139018, ids_seed=16892041, scenario_seed=7349172,
                group_by=['context_family', 'full_profile', 'authored_split_specific_phrase_parent'],
                review='240 stratified texts plus authored source-module review; no independent human review',
                minima='At least 24 mentions of every taxonomy type per split; both positive and negative sessions per nonzero cell',
                zero_people_negative_only=True, old_corpora_used=False)
    write(output/'generation-plan.json', plan)
    rng, lrng, irng = random.Random(plan['values_seed']), random.Random(plan['layout_seed']), random.Random(plan['ids_seed'])
    srng = random.Random(plan['scenario_seed'])
    names = name_pool(rng)
    ids = list(range(100000, 999999))
    irng.shuffle(ids)
    records, results, enrichments = [], {}, {}
    calculator = Calculator()
    for split in SPLITS:
        for people in range(4):
            for bucket in ('short', 'long'):
                # Sixty planned cases: four examples for each document type,
                # four for each of the eleven non-document mechanisms.
                schedule = [('document', field) for field in ('inn', 'snils', 'card', 'passport') for _ in range(4)]
                schedule += [(mode, 'inn' if i % 2 else 'snils') for mode in MODES if mode != 'document' for i in range(4)]
                schedule *= COUNTS[split] // 60
                srng.shuffle(schedule)
                for repetition in range(COUNTS[split]):
                    family_index = repetition % len(CONTEXTS[split])
                    family, opening = CONTEXTS[split][family_index]
                    variant = repetition // len(CONTEXTS[split])
                    mode, document = schedule[repetition]
                    if not people:
                        mode = variant % 5
                    length = lrng.randrange(2, 6) if bucket == 'short' else lrng.randrange(6, 9)
                    templates = layout(story(split, opening, people, mode, document), length, split, lrng)
                    values = independent_profiles(rng, names)
                    messages, entities, services, controls = materialize(templates, values)
                    sid = f'synth-{ids.pop()}'
                    doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages, entities=entities,
                               case_flags=dict(is_example=False, annotation_uncertain=False, clarification_reason=None))
                    enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
                    result = assess(doc, calculator, address_enrichment=enrichment)
                    doc['case_flags'] = result['case_flags']
                    enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
                    result = assess(doc, calculator, address_enrichment=enrichment)
                    row = dict(session_id=sid, split=split, people_count=people, length_group=bucket,
                               family=family, mode=str(mode), document_variant=document,
                               title=f'{family}: {mode}', difficulty='unclassified', difficulty_reason='Grouped by people and length',
                               profile_keys=[values[p]['first']+' '+values[p]['last'] for p in 'abcs'],
                               gold=doc, gold_service_contacts=services, negative_controls=controls,
                               expected_first_alert=result['first_alert']['message_index'] if result['first_alert'] else None,
                               final_uncertain=result['messages'][-1]['annotation_uncertain'])
                    verify_labels(row)
                    records.append(row)
                    results[sid], enrichments[sid] = result, enrichment
    irng.shuffle(records)
    references = dict(policy=calculator.policy, geography=calculator.geo, housing=calculator.housing,
                      occupations=calculator.occupations.reference, occupation_mapping=calculator.occupations.mapping)
    manifest = dict(version=VERSION, synthetic_only=True, generation_policy=plan['generation_policy'], benchmark_ready=False,
                    annotation_source='authored_template_spans_owners_plus_calculator', independent_human_review=False,
                    service_weights_used_for_gold=False, annotation_policy_version=calculator.policy['version'],
                    references={key: hash_value(value) for key, value in references.items()},
                    code_hashes={name: hashlib.sha256((ROOT/'tools'/name).read_bytes()).hexdigest() for name in
                                 ['build_session_benchmark.py', 'session_benchmark_blueprints.py', 'annotation_contract.py',
                                  'annotation_calculator.py', 'build_medium_scenario_examples.py']})
    write(output/'manifest.json', manifest)
    write(output/'snapshot.json', dict(**manifest, records=records))
    write(output/'calculator-assessments.json', results)
    write(output/'address-enrichment.json', enrichments)
    sample = []
    for split in SPLITS:
        subset = [r for r in records if r['split'] == split]
        write(output/split/'sessions.json', [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in subset])
        write(output/split/'gold.json', [r['gold'] for r in subset])
        write(output/split/'snapshot.json', dict(**manifest, records=subset))
        ner_rows = []
        for row in subset:
            for message in row['gold']['messages']:
                spans = [{k: e[k] for k in ('start', 'end', 'type')} for e in row['gold']['entities'] if e['message_id'] == message['id']]
                ner_rows.append(dict(session_id=row['session_id'], message_id=message['id'], text=message['text'], spans=spans))
        jsonl(output/split/'ner.jsonl', ner_rows)
        jsonl(output/split/'linker-supervised.jsonl', linker_rows(subset))
        jsonl(output/split/'alerts.jsonl', [dict(session_id=r['session_id'], first_alert=results[r['session_id']]['first_alert'],
                                               prefixes=results[r['session_id']]['messages']) for r in subset])
        for people in range(4):
            for bucket in ('short', 'long'):
                cell = [r for r in subset if r['people_count'] == people and r['length_group'] == bucket]
                write(output/split/f'people-{people}'/bucket/'session-ids.json', [r['session_id'] for r in cell])
                # Maximise mode diversity first, then fill to fixed sample quota.
                chosen, seen = [], set()
                for row in cell:
                    if row['mode'] not in seen:
                        chosen.append(row)
                        seen.add(row['mode'])
                    if len(chosen) == 10:
                        break
                chosen += [r for r in cell if r not in chosen][:10-len(chosen)]
                sample.extend(chosen)
    write(output/'review-sample.json', dict(**manifest, records=sample))
    (output/'review-sample.md').write_text(render(sample, results, 'Выборка для ревью нового benchmark'), encoding='utf-8')
    report = audit(output)
    write(output/'validation-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must remain under data/')
    report = audit(args.output) if args.verify_only else build(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
