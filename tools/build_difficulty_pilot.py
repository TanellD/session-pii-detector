"""Сборка авторского пилота по трём сложностям из заранее разделённого плана."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from annotation_address_enrichment import build_enrichment
from annotation_calculator import Calculator
from annotation_contract import assess, validate
from build_medium_scenario_examples import TYPES, profile, apartment_confirmation
from benchmarks.pii_smoke.regex_detector import valid_inn, valid_snils, valid_luhn

VERSION = 'difficulty-pilot-90-author-v3'
TYPES = {**TYPES, 'passport': 'passport'}
SLOT = re.compile(r'\[\[(a|b|c|ab|ac|bc|u|s):([a-z_]+)\]\]')
NAMES = ['Иван', 'Алексей', 'Мария', 'Ольга', 'Павел', 'Анна', 'Сергей', 'Елена']
SURNAMES = ['Макетолесов', 'Проболугов', 'Шаблоногорский', 'Тестореченский', 'Демополянский',
    'Образосветов', 'Макетобережный', 'Прободолинский', 'Шаблонолесов', 'Тестополевой',
    'Демолучев', 'Образоградский', 'Макетовершинский', 'Пробосадов', 'Шаблонореченский',
    'Тестолугов', 'Демозёрский', 'Образобережный', 'Макетосадов', 'Проболучев',
    'Шаблонодолинский', 'Тестовершинский', 'Демолесов', 'Образополевой', 'Макетоградский',
    'Пробовершинский', 'Шаблоносветов', 'Тестобережный', 'Демосадов', 'Образолугов']
NEGATIVE_FIELDS = {'bad_phone', 'bad_snils', 'masked_card', 'masked_phone', 'bad_inn'}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def profiles_for(family_index, difficulty_index):
    profiles = {}
    for offset, owner in enumerate(('a', 'b', 'c', 's')):
        number = 500+family_index*12+difficulty_index*4+offset
        item = profile(number)
        first = NAMES[(family_index+offset) % len(NAMES)]
        female = first in {'Мария', 'Ольга', 'Анна', 'Елена'}
        surname = SURNAMES[family_index]
        if female:
            surname = surname[:-2]+'ая' if surname.endswith(('ий', 'ый', 'ой')) else surname+'а'
        item.update(first=first, last=surname, patronymic='Макетовна' if female else 'Макетович',
            last_new='Ново'+surname.lower(),
            sex='женщина' if female else 'мужчина', phone=f'+7 (000) {number:03d}-89-01',
            phone_new=f'+7 (000) {number:03d}-89-02', email=f'pilot{number}@example.invalid',
            email_name=f'mail.{number}@example.invalid', organization=f'ООО «Пробный контур {number}»',
            organization_name=f'ООО «{first}»', masked_phone='+7 (000) ***-**-01')
        item['bad_inn'] = item['inn'][:-1]+str((int(item['inn'][-1])+1) % 10)
        item['passport'] = f'00 00 {number:06d}'
        profiles[owner] = item
    for pair in ('ab', 'ac', 'bc'):
        profiles[pair] = dict(profiles[pair[0]])
    profiles['u'] = dict(profiles['b'])
    return profiles


def materialize(templates, profiles):
    messages, entities, service_ids, controls = [], [], [], []
    for index, template in enumerate(templates, 1):
        text, offset, mid = '', 0, f'm{index}'
        for match in SLOT.finditer(template):
            text += template[offset:match.start()]
            owner, slot = match.groups()
            value = profiles[owner][slot]
            start = len(text)
            text += value
            if slot in TYPES:
                owners = None if owner in {'u', 's'} else list(owner) if len(owner) == 2 else owner
                entity = dict(id=f'e{len(entities)+1}', message_id=mid, start=start, end=len(text),
                    type=TYPES[slot], person_id=owners)
                entities.append(entity)
                if owner == 's':
                    if slot not in {'phone', 'email'}:
                        raise ValueError('Only contact can be marked service')
                    service_ids.append(entity['id'])
            elif slot in NEGATIVE_FIELDS:
                controls.append(dict(message_id=mid, start=start, end=len(text), kind=slot))
            else:
                raise ValueError('Unknown annotation slot: '+slot)
            offset = match.end()
        text += template[offset:]
        if '[[' in text or ']]' in text:
            raise ValueError('Unresolved annotation marker')
        messages.append(dict(id=mid, text=text))
    return messages, entities, service_ids, controls


def layout_messages(templates, expected, difficulty, family_index):
    """Варьируем границы реплик, сохраняя порядок и авторский триггер."""
    parts = [(text, [i]) for i, text in enumerate(templates, 1)]
    choice = family_index % 3
    if difficulty == 'hard' and choice < 2:
        groups = [[0, 1], [2, 3], [4], [5], [6]] if choice == 0 else [[0, 1], [2], [3], [4], [5], [6]]
        parts = [(' '.join(templates[i] for i in group), [i+1 for i in group]) for group in groups]
    elif difficulty == 'medium' and choice == 0:
        parts = [(templates[0]+' '+templates[1], [1, 2]), *parts[2:]]
    elif difficulty == 'easy' or (difficulty == 'medium' and choice == 2):
        target = 3+choice if difficulty == 'easy' else 6
        while len(parts) < target:
            # Не разбиваем реплику исходного первого алерта: только её
            # нейтральное продолжение могло бы ошибочно сдвинуть контроль.
            selected = next((i for i, (text, origins) in enumerate(parts)
                if expected not in origins and re.search(r'(?<=[.!?])\s+', text)), None)
            if selected is None:
                break
            text, origins = parts[selected]
            left, right = re.split(r'(?<=[.!?])\s+', text, maxsplit=1)
            parts[selected:selected+1] = [(left, origins), (right, origins)]
    mapped = next((i for i, (_, origins) in enumerate(parts, 1) if expected in origins), None)
    return [text for text, _ in parts], mapped, [origins for _, origins in parts]


def verify_labels(record):
    doc = record['gold']
    texts = validate(doc)
    validators = {'individual_inn': valid_inn, 'snils': valid_snils, 'bank_card': valid_luhn}
    for entity in doc['entities']:
        check = validators.get(entity['type'])
        if check and not check(texts[entity['message_id']][entity['start']:entity['end']]):
            raise ValueError('Invalid document annotated as complete')
    for control in record['negative_controls']:
        value = texts[control['message_id']][control['start']:control['end']]
        if control['kind'] == 'bad_snils' and valid_snils(value):
            raise ValueError('Negative checksum control is valid')
        if control['kind'] == 'bad_inn' and valid_inn(value):
            raise ValueError('Negative checksum control is valid')
        if any(e['message_id'] == control['message_id'] and
               e['start'] < control['end'] and control['start'] < e['end'] for e in doc['entities']):
            raise ValueError('Negative format control annotated as complete')


def render(records, assessments, title):
    lines = ['# '+title, '', 'Авторская синтетическая разметка; независимого ревью нет. '
        'Сервис и Gemma не запускались. Эталонные алерты вычислены существующим калькулятором. '
        'Отрицательные метки с нехваткой статистики не доказывают безопасность.', '']
    for r in records:
        doc, result = r['gold'], assessments[r['session_id']]
        lines.extend([f'## {r["session_id"]} — {r["title"]}', '',
            f'Часть: `{r["split"]}`; сложность: `{r["difficulty"]}`; механизм: {r["difficulty_reason"]}.', ''])
        for i, message in enumerate(doc['messages'], 1):
            lines.append(f'{i}. {message["text"]}')
        first = result['first_alert']
        lines.extend(['', f'Первый алерт: **{first["message_index"]}**, `{first["reason"]}`.' if first else
            'Первый алерт: **нет** по рабочей политике.',
            f'Недостаточно статистики на последнем шаге: **{"да" if result["messages"][-1]["annotation_uncertain"] else "нет"}**.', '',
            '| Сообщение | Фрагмент | Тип | Владелец |', '|---|---|---|---|'])
        texts = {m['id']: m['text'] for m in doc['messages']}
        for e in doc['entities']:
            value = texts[e['message_id']][e['start']:e['end']]
            owner = json.dumps(e['person_id'], ensure_ascii=False)
            service = ' (служебный)' if e['id'] in r['gold_service_contacts'] else ''
            lines.append(f'| {e["message_id"]} | {value} | `{e["type"]}` | `{owner}`{service} |')
        if not doc['entities']:
            lines.append('| — | Нет поддерживаемых атрибутов | — | — |')
        for control in r['negative_controls']:
            value = texts[control['message_id']][control['start']:control['end']]
            lines.extend(['', f'Без спана полного документа/контакта: `{value}` — `{control["kind"]}`.'])
        lines.append('')
    return '\n'.join(lines)


def load_plan(path):
    content = path.read_text(encoding='utf-8')
    if path.suffix == '.json' or content.lstrip().startswith('{'):
        return json.loads(content)
    cases, case, level = [], None, None
    for line in content.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if line.startswith('@ '):
            family, split, category, title = [v.strip() for v in line[2:].split('|')]
            case = dict(family=family, split=split, category=category, title=title, levels={})
            cases.append(case)
        elif line.startswith('! '):
            difficulty, expected, mechanism = [v.strip() for v in line[2:].split('|')]
            level = dict(expected_first_alert=None if expected == 'null' else int(expected), mechanism=mechanism, messages=[])
            case['levels'][difficulty] = level
        else:
            if level is None:
                raise ValueError('Message outside authored scenario')
            level['messages'].append(line)
    return dict(generation_policy='synthetic-session-generation-v1', dataset_version=VERSION,
        purpose='combined', session_count=90, language='ru', message_roles='user_only',
        generator='authored', complexity_mix=dict(easy=1/3, medium=1/3, hard=1/3),
        complexity_counts=dict(easy=30, medium=30, hard=30),
        split=dict(mode='grouped_train_dev_test', per_difficulty=dict(train=16, validation=7, test=7),
            group_by=['scenario_family', 'profile_family', 'paraphrase_parent'], parent_datasets=[]),
        references='Existing calculator working policy and local references, pinned in manifest',
        review_plan='Assistant author review of all dialogues and annotations; no independent reviewer',
        source_format='@ family|split|category|title; ! difficulty|expected_first_alert|mechanism; one message per line',
        cases=cases)


def build(plan_path, output):
    if output.exists():
        raise ValueError('Use a new directory; frozen datasets are not overwritten')
    plan = load_plan(plan_path)
    cases = plan['cases']
    if len(cases) != 30 or len({c['family'] for c in cases}) != 30:
        raise ValueError('Expected 30 distinct scenario families')
    if Counter(c['split'] for c in cases) != {'train': 16, 'validation': 7, 'test': 7}:
        raise ValueError('Unexpected preassigned split sizes')
    calculator = Calculator()
    records, assessments, enrichments, profile_groups = [], {}, {}, {}
    for family_index, case in enumerate(cases):
        for difficulty_index, difficulty in enumerate(('easy', 'medium', 'hard')):
            variant = case['levels'][difficulty]
            profiles = profiles_for(family_index, difficulty_index)
            templates, expected, layout = layout_messages(variant['messages'], variant['expected_first_alert'], difficulty, family_index)
            messages, entities, service_ids, controls = materialize(templates, profiles)
            sid = f'pilot90-{difficulty}-{family_index+1:02d}'
            doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages, entities=entities,
                case_flags=dict(is_example=False, annotation_uncertain=False, clarification_reason=None))
            enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
            result = assess(doc, calculator, address_enrichment=enrichment)
            doc['case_flags'] = result['case_flags']
            enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
            result = assess(doc, calculator, address_enrichment=enrichment)
            actual = result['first_alert']['message_index'] if result['first_alert'] else None
            if actual != expected:
                raise ValueError(f'Authored prefix control disagrees: {sid}, expected={expected}, calculated={actual}')
            record = dict(session_id=sid, family=case['family'], title=case['title'], scenario_category=case['category'],
                split=case['split'], difficulty=difficulty, difficulty_reason=variant['mechanism'],
                profile_family=case['family'], paraphrase_parent=case['family'], gold=doc,
                source_paragraph_layout=layout,
                expected_first_alert=actual, gold_service_contacts=service_ids, negative_controls=controls,
                review_status='author_reviewed_no_independent_review',
                label_policy_version=calculator.policy['version'],
                final_uncertain=result['messages'][-1]['annotation_uncertain'])
            verify_labels(record)
            records.append(record)
            assessments[sid], enrichments[sid] = result, enrichment
            # Full name and contacts identify a generated profile group; individual
            # first names can naturally repeat and are not proof of equal identity.
            for owner in ('a', 'b', 'c', 's'):
                profile_key = (profiles[owner]['first'], profiles[owner]['last'])
                previous = profile_groups.setdefault(profile_key, case['split'])
                if previous != case['split']:
                    raise ValueError('Full generated name reused across partitions')
    if len({tuple(m['text'] for m in r['gold']['messages']) for r in records}) != len(records):
        raise ValueError('Duplicate dialogue')
    split_ids = {part: [r['session_id'] for r in records if r['split'] == part] for part in ('train', 'validation', 'test')}
    def stats(rows):
        return dict(sessions=len(rows), messages=sum(len(r['gold']['messages']) for r in rows),
            lengths=dict(Counter(len(r['gold']['messages']) for r in rows)),
            entities=sum(len(r['gold']['entities']) for r in rows),
            positive=sum(r['expected_first_alert'] is not None for r in rows),
            negative_without_final_uncertainty=sum(r['expected_first_alert'] is None and not r['final_uncertain'] for r in rows),
            negative_with_final_uncertainty=sum(r['expected_first_alert'] is None and r['final_uncertain'] for r in rows),
            split=dict(Counter(r['split'] for r in rows)))
    manifest = dict(version=VERSION, synthetic_only=True, generation_policy='synthetic-session-generation-v1',
        annotation_schema='annotation-mvp-v2', annotation_policy_version=calculator.policy['version'],
        taxonomy_version=calculator.policy['taxonomy_version'], annotation_source='assistant_authored_spans_and_owners_plus_calculator',
        independent_review=False, service_weights_used_for_gold=False, model_inference_performed=False,
        message_roles='user_only', generator='authored_difficulty_variants_with_deterministic_synthetic_slots',
        overall=stats(records), by_difficulty={d: stats([r for r in records if r['difficulty'] == d]) for d in ('easy', 'medium', 'hard')},
        split_ids=split_ids, family_split={c['family']: c['split'] for c in cases},
        plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        materializer_sha256=hashlib.sha256((ROOT/'tools/build_medium_scenario_examples.py').read_bytes()).hexdigest(),
        references={name: digest(value) for name, value in dict(policy=calculator.policy, geography=calculator.geo,
            housing=calculator.housing, occupation=calculator.occupations.reference, mapping=calculator.occupations.mapping).items()},
        limitations=['Pilot partitions are too small for final quality conclusions.',
            'Train, validation and test share broad evaluation categories, but not authored scenario families or generated profiles.',
            'Difficulty assigned by explicit context mechanics; not empirically calibrated.',
            'Authoring and annotation by the same assistant; no independent review.',
            'Negative labels with insufficient statistics do not establish safety.',
            'Pure negative controls can have no named people; participant counts are hidden metadata.',
            'Fictional city has no invented population; apartment type explicitly author-confirmed.'])
    output.mkdir(parents=True)
    def write(name, value):
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write('generation-plan.json', plan)
    (output/'authored-plan.txt').write_text(plan_path.read_text(encoding='utf-8'), encoding='utf-8')
    write('manifest.json', manifest)
    write('snapshot.json', dict(**manifest, records=records))
    write('gold.json', [r['gold'] for r in records])
    write('sessions.json', [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in records])
    write('calculator-assessments.json', assessments)
    write('address-enrichment.json', enrichments)
    for difficulty in ('easy', 'medium', 'hard'):
        subset = [r for r in records if r['difficulty'] == difficulty]
        (output/f'{difficulty}-for-review.md').write_text(render(subset, assessments, f'Пилот — {difficulty}'), encoding='utf-8')
        for part in ('train', 'validation', 'test'):
            selected = [r for r in subset if r['split'] == part]
            write(f'{difficulty}/{part}/snapshot.json', dict(version=VERSION, synthetic_only=True,
                difficulty=difficulty, split=part, label_policy_version=calculator.policy['version'],
                source_manifest='../../manifest.json', records=selected))
            write(f'{difficulty}/{part}/sessions.json', [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in selected])
            write(f'{difficulty}/{part}/gold.json', [r['gold'] for r in selected])
    lines = ['# Пилот трёх уровней сложности', '',
        'Авторская синтетическая разметка; алерты рассчитаны калькулятором. '
        'Это структурный пилот, не окончательный benchmark качества. Сервис и Gemma не запускались.', '',
        '| Сложность | Train | Validation | Test | Положительные | Отрицательные без итоговой неопределённости | Отрицательные с неопределённостью |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for difficulty, counts in manifest['by_difficulty'].items():
        parts = counts['split']
        lines.append(f'| [{difficulty}]({difficulty}-for-review.md) | {parts["train"]} | {parts["validation"]} | {parts["test"]} | '
            f'{counts["positive"]} | {counts["negative_without_final_uncertainty"]} | {counts["negative_with_final_uncertainty"]} |')
    lines.extend(['', 'Части назначены сценарным семействам до материализации; варианты одного семейства '
        'всех сложностей остаются в одной части. Полные сгенерированные имена и контакты между частями не повторяются. '
        'Широкие категории проверки могут быть общими.', '',
        'Каталоги easy/, medium/, hard/ содержат отдельные train/, validation/, test/. '
        'Для входа модели используйте только sessions.json. snapshot.json и gold.json содержат эталон и не передаются модели.', '',
        'Сохранены [план](generation-plan.json), [манифест](manifest.json), '
        '[расчёты](calculator-assessments.json), [адресные подтверждения](address-enrichment.json). '
        'Исходная авторская разметка не является независимым ревью.', ''])
    (output/'overview.md').write_text('\n'.join(lines), encoding='utf-8')
    return manifest


def audit(output):
    """Повторная проверка сохранённого набора без генерации и модельных вызовов."""
    read = lambda name: json.loads((output/name).read_text(encoding='utf-8'))
    snapshot = read('snapshot.json')
    records, enrichments = snapshot['records'], read('address-enrichment.json')
    calculator = Calculator()
    families, profiles, contacts, dialogues = {}, {}, {}, set()
    for record in records:
        verify_labels(record)
        doc = record['gold']
        texts = validate(doc)
        result = assess(doc, calculator, address_enrichment=enrichments[record['session_id']])
        actual = result['first_alert']['message_index'] if result['first_alert'] else None
        if actual != record['expected_first_alert'] or result['messages'][-1]['annotation_uncertain'] != record['final_uncertain']:
            raise ValueError('Saved calculator labels differ')
        if result != read('calculator-assessments.json')[record['session_id']]:
            raise ValueError('Saved prefix assessments differ')
        for grouping, key in [(families, record['family']), (profiles, record['profile_family'])]:
            if grouping.setdefault(key, record['split']) != record['split']:
                raise ValueError('Group overlap across partitions')
        whole = tuple(m['text'] for m in doc['messages'])
        if whole in dialogues or any('[[' in text or ']]' in text or '\ufffd' in text for text in whole):
            raise ValueError('Duplicate dialogue or unresolved marker')
        dialogues.add(whole)
        for entity in doc['entities']:
            if entity['type'] in {'phone', 'email'}:
                key = entity['type'], texts[entity['message_id']][entity['start']:entity['end']]
                if contacts.setdefault(key, record['split']) != record['split']:
                    raise ValueError('Contact overlap across partitions')
    for difficulty in ('easy', 'medium', 'hard'):
        for part, count in [('train', 16), ('validation', 7), ('test', 7)]:
            rows = [r for r in records if r['difficulty'] == difficulty and r['split'] == part]
            source = read(f'{difficulty}/{part}/sessions.json')
            expected = [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in rows]
            if len(rows) != count or source != expected or read(f'{difficulty}/{part}/gold.json') != [r['gold'] for r in rows]:
                raise ValueError('Partition export differs from frozen source')
            if read(f'{difficulty}/{part}/snapshot.json')['records'] != rows:
                raise ValueError('Partition snapshot differs from frozen source')
    return dict(status='passed', sessions=len(records), checks=['schema_and_spans', 'checksum_controls',
        'calculator_prefix_replay', 'address_sidecar_hashes', 'disjoint_family_profile_contact_partitions',
        'distinct_dialogues', 'partition_exports_without_gold_leakage'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must remain outside git in data/')
    if args.verify_only:
        print(json.dumps(audit(args.output), ensure_ascii=False, indent=2))
    else:
        if not args.plan:
            parser.error('--plan is required for generation')
        result = build(args.plan, args.output)
        validation = audit(args.output)
        (args.output/'validation-report.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(dict(manifest=result, validation=validation), ensure_ascii=False, indent=2))
