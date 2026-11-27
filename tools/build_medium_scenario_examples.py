"""Материализация авторского плана примеров с разметкой и расчётом алертов."""
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
from benchmarks.pii_smoke.regex_detector import valid_inn, valid_luhn, valid_snils

VERSION = 'medium-scenario-examples-33-v1'
SLOT = re.compile(r'\[\[(a|b|ab|u|s):([a-z_]+)\]\]')
TYPES = dict(first='first_name', last='last_name', patronymic='patronymic', phone='phone',
    email='email', address='address', occupation='occupation', organization='organization',
    organization_name='organization', age='age', sex='sex', geography='geography',
    inn='individual_inn', card='bank_card', snils='snils', phone_new='phone', last_new='last_name', email_name='email')
FIRST = ['Тестимир', 'Макетослав', 'Пробомир', 'Образимир', 'Демослав', 'Шабломир']
LAST = ['Макетонов', 'Пробоградов', 'Шаблонов', 'Тестоградов', 'Образцовский', 'Демонов']


def profile(n):
    first = FIRST[n % len(FIRST)]
    inn_base = f'0000{n:06d}'
    inn = next(inn_base+f'{tail:02d}' for tail in range(100) if valid_inn(inn_base+f'{tail:02d}'))
    snils_base = f'{n:09d}'
    snils = next(snils_base+f'{tail:02d}' for tail in range(100) if valid_snils(snils_base+f'{tail:02d}'))
    card_base = f'00000000{n:07d}'
    card = next(card_base+str(tail) for tail in range(10) if valid_luhn(card_base+str(tail)))
    bad_snils = snils[:-1]+str((int(snils[-1])+1) % 10)
    assert valid_inn(inn) and valid_luhn(card) and valid_snils(snils) and not valid_snils(bad_snils)
    return dict(first=first, last=LAST[n % len(LAST)], patronymic='Макетович',
        phone=f'+7 (000) {n:03d}-12-34', email=f'profile{n}@example.invalid',
        phone_new=f'+7 (000) {n:03d}-56-78', last_new='Пробомакетов', email_name=f'testimir.{n}@example.invalid',
        address=f'г. Пробоград, ул. Макетная, д. {n}, кв. 7',
        occupation='бухгалтер', organization=f'ООО «Макетный проект {n}»',
        organization_name=f'ООО «{first}»', age=str(25+n % 20), sex='мужчина', geography='Пробоград',
        inn=inn, snils=f'{snils[:3]}-{snils[3:6]}-{snils[6:9]} {snils[9:]}',
        bad_snils=f'{bad_snils[:3]}-{bad_snils[3:6]}-{bad_snils[6:9]} {bad_snils[9:]}',
        card=' '.join(card[i:i+4] for i in range(0, 16, 4)), bad_phone='abc12', masked_card='0000 **** **** 1234')


def materialize(templates, profiles):
    messages, entities, service_ids, controls = [], [], [], []
    for index, template in enumerate(templates, 1):
        mid, text, offset = f'm{index}', '', 0
        for match in SLOT.finditer(template):
            text += template[offset:match.start()]
            owner, slot = match.groups()
            value = profiles[owner][slot]
            start = len(text)
            text += value
            if slot in TYPES:
                entity = dict(id=f'e{len(entities)+1}', message_id=mid, start=start, end=len(text),
                    type=TYPES[slot], person_id=['a', 'b'] if owner == 'ab' else None if owner in {'u', 's'} else owner)
                entities.append(entity)
                if owner == 's':
                    if slot not in {'phone', 'email'}:
                        raise ValueError('Service contact must be phone or email')
                    service_ids.append(entity['id'])
            else:
                controls.append(dict(message_id=mid, start=start, end=len(text), slot=slot,
                    expected_entity=False, reason='invalid_checksum_or_incomplete_format'))
            offset = match.end()
        text += template[offset:]
        if '[[' in text or ']]' in text:
            raise ValueError('Unresolved template slot')
        messages.append(dict(id=mid, text=text))
    return messages, entities, service_ids, controls


def apartment_confirmation(query):
    if 'кв.' not in query:
        return dict(status='unresolved', reason='housing_type_not_confirmed')
    return dict(status='estimated',
        address_fact=dict(housing_type='apartment', reference_region='Российская Федерация', resolved_level='apartment'),
        assumptions=['Автор подтверждает квартиру проживания в вымышленном сценарии; не геокодирование.',
                     'Применяется среднее из рабочей политики, фактическая заселённость не измерена.'],
        building=dict(source='author_confirmed_synthetic_residential_apartment'))


def build(plan_path, output):
    if output.exists():
        raise ValueError('Use a new output directory')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    calculator = Calculator()
    records, assessments, enrichments = [], {}, {}
    for index, case in enumerate(plan['cases'], 1):
        profiles = dict(a=profile(index*3), b=profile(index*3+1), s=profile(index*3+2))
        profiles['ab'] = dict(profiles['a'])
        profiles['u'] = dict(profiles['b'])
        messages, entities, service_ids, controls = materialize(case['messages'], profiles)
        sid = f'medium-33-{index:03d}'
        doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages, entities=entities,
            case_flags=dict(is_example=False, annotation_uncertain=False, clarification_reason=None))
        validate(doc)
        enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
        assessed = assess(doc, calculator, address_enrichment=enrichment)
        doc['case_flags'] = assessed['case_flags']
        # Sidecar must match the saved annotation, including its reviewed case flags.
        enrichment = build_enrichment(doc, calculator, resolver=apartment_confirmation)
        assessed = assess(doc, calculator, address_enrichment=enrichment)
        first = assessed['first_alert']
        record = dict(session_id=sid, family=case['family'], title=case['title'], complexity='medium',
            complexity_reason=case['complexity_reason'], profile_family=f'authored-example-{index}',
            review_status='author_reviewed_not_independently_verified', gold=doc,
            expected_first_alert=first['message_index'] if first else None,
            gold_service_contacts=service_ids, negative_controls=controls,
            author_note=case.get('note'), label_policy_version=calculator.policy['version'])
        if 'expected_first_alert' in case and record['expected_first_alert'] != case['expected_first_alert']:
            raise ValueError(f'Calculator disagrees with authored control: {sid}')
        records.append(record)
        assessments[sid], enrichments[sid] = assessed, enrichment
    counts = Counter(r['family'] for r in records)
    if len(counts) != 11 or set(counts.values()) != {3}:
        raise ValueError('Expected three examples for each of eleven scenario families')
    manifest = dict(dataset_version=VERSION, generation_policy='synthetic-session-generation-v1',
        synthetic_only=True, generator='authored_dialogue_templates_with_deterministic_synthetic_slots',
        sessions=len(records), messages=sum(len(r['gold']['messages']) for r in records),
        entities=sum(len(r['gold']['entities']) for r in records), families=dict(counts),
        complexity='medium', split='development_only', message_roles='user_only',
        positive_sessions=sum(r['expected_first_alert'] is not None for r in records),
        uncertain_at_end=sum(a['messages'][-1]['annotation_uncertain'] for a in assessments.values()),
        annotation_policy_version=calculator.policy['version'], service_weights_used_for_gold=False,
        plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        reference_sha256={name: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            for name, value in dict(policy=calculator.policy, geography=calculator.geo, housing=calculator.housing,
                occupation=calculator.occupations.reference, occupation_mapping=calculator.occupations.mapping).items()},
        limitations=['Examples for author review, not an independent benchmark.',
            'Difficulty is assigned by scenario mechanics, not measured on a model.',
            'Pure negative controls have no named people; other examples introduce two people.',
            'Fictional geography remains statistically unresolved.',
            'No service or LLM inference performed in this build.'])
    output.mkdir(parents=True)
    def write(name, value):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write('generation-plan.json', plan)
    write('manifest.json', manifest)
    write('snapshot.json', dict(**manifest, records=records))
    write('sessions.json', [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in records])
    write('gold.json', [r['gold'] for r in records])
    write('calculator-assessments.json', assessments)
    write('address-enrichment.json', enrichments)
    lines = ['# Примеры сценариев средней сложности', '',
        'По три авторских диалога для каждого семейства политики генерации. '
        'Тексты, значения и профили синтетические. Это примеры для ревью, сервис и LLM на них не запускались.', '',
        'Разметка дана по доступному префиксу; старые null не переназначаются. '
        'Алерты рассчитаны существующим калькулятором. Неуверенная отрицательная метка не доказывает безопасность.', '']
    for family in counts:
        lines.extend([f'## {family}', ''])
        for row in [r for r in records if r['family'] == family]:
            sid, doc = row['session_id'], row['gold']
            lines.extend([f'### {sid} — {row["title"]}', '',
                f'Средняя сложность: {row["complexity_reason"]}.', ''])
            for i, message in enumerate(doc['messages'], 1):
                lines.append(f'{i}. {message["text"]}')
            result = assessments[sid]
            first = result['first_alert']
            lines.extend(['', f'**Первый алерт:** сообщение {first["message_index"]}, `{first["reason"]}`.' if first else
                '**Первый алерт:** отсутствует по рабочей политике.',
                f'**Недостаток статистики на последнем шаге:** {"да" if result["messages"][-1]["annotation_uncertain"] else "нет"}.', '',
                '| Сообщение | Фрагмент | Тип | Владелец |', '|---|---|---|---|'])
            texts = {m['id']: m['text'] for m in doc['messages']}
            for entity in doc['entities']:
                owner = json.dumps(entity['person_id'], ensure_ascii=False)
                if entity['id'] in row['gold_service_contacts']:
                    owner += ' (служебный контакт)'
                value = texts[entity['message_id']][entity['start']:entity['end']]
                lines.append(f'| {entity["message_id"]} | {value} | `{entity["type"]}` | `{owner}` |')
            if not doc['entities']:
                lines.append('| — | Нет поддерживаемых личных атрибутов | — | — |')
            if row.get('author_note'):
                lines.extend(['', row['author_note']])
            for control in row['negative_controls']:
                value = texts[control['message_id']][control['start']:control['end']]
                lines.extend(['', f'Контроль: `{value}` ({control["message_id"]}) не размечается полным документом/телефоном: `{control["slot"]}`.'])
            lines.append('')
    (output/'examples-for-review.md').write_text('\n'.join(lines), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must remain in data/')
    print(json.dumps(build(args.plan, args.output), ensure_ascii=False, indent=2))
