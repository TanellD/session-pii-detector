"""Авторский корпус сочетаний атрибутов, без вычисления меток текущими весами."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
from annotation_contract import validate
from build_complex_200 import profile as synthetic_profile, EXTRAS, FLAGS

VERSION = 'attribute-combinations-300-v1'
POLICY = 'author-proposed-attribute-bundles-v1'
SLOT = re.compile(r'\{(a|b|c|ab|s):([a-z_]+)\}')
TYPES = dict(first='first_name', last='last_name', phone='phone', email='email', address='address',
             age='age', sex='sex', geography='geography', occupation='occupation', organization='organization')
FIRST_NAMES = ['Илья', 'Егор', 'Марк', 'Олег', 'Вера', 'Нина', 'Лада', 'Алина', 'Антон', 'Кира', 'Роман', 'Полина']
JOBS = ['редактором', 'инженером', 'бухгалтером', 'дизайнером', 'корректором', 'лаборантом']
EMPLOYERS = ['ООО «Макетная лаборатория»', 'ООО «Пробный цех»', 'АО «Тестовый проект»',
             'ООО «Образцовая мастерская»', 'ООО «Демонстрационный центр»']
CITIES = ['Пробоград', 'Макетск', 'Тестоград', 'Демогорск']
CONTEXTS = [
 'Пишу по анкете на обучение. Для второй записи нужно сохранить отдельную анкету.',
 'Пишу по пропуску на площадку. Есть две записи, их нужно проверить отдельно.',
 'Уточняю сведения в кабинете для корпоративной программы. Совместного профиля у нас нет.',
 'Пишу по заявкам на обслуживание. Данные каждой заявки должны остаться у своего заявителя.',
 'Нужно обновить анкеты участников. Не переносите сведения из одной записи в другую.',
 'По записи на занятие хочу проверить заполнение полей. Записей несколько, условия могут отличаться.',
]
CONTACT = dict(phone='Мой личный телефон {a:phone}. Уведомление о статусе можно передать мне.',
               email='Моя личная почта {a:email}. Подтверждение по моей записи прошу отправить туда.',
               address='Мой адрес проживания {a:address}. Это сведения моей анкеты, не место проведения встречи.')
FIELD = dict(age='Мой возраст — {a:age}. В анкете нужно сохранить именно это значение.',
             sex='Пол в моей анкете — {a:sex}. У второй записи сведения могут отличаться.',
             geography='Город в моей анкете — {a:geography}. Адрес улицы здесь не указываю.',
             occupation='Я работаю {a:occupation}. Это моя профессия, ко второй записи она не относится.',
             organization='Мой работодатель — {a:organization}. Укажите его только в моей записи.')

# Метка задаётся авторской рабочей гипотезой, не результатом runtime-risk-v2.
# Для этой гипотезы нужны ревью и согласование перед подбором production-весов.
ORANGE_BUNDLES = [
 ('first_only', [], False, 'Одного имени и одного контакта недостаточно по авторской рабочей гипотезе'),
 ('first_age', ['age'], False, 'Имя и возраст с одним контактом: отрицательная граница авторской гипотезы'),
 ('demographic', ['age', 'sex'], False, 'Имя, возраст и пол — широкие демографические сведения; авторская гипотеза без алерта'),
 ('employment', ['occupation', 'organization'], True, 'Именованный профессиональный профиль с точным работодателем и контактом; авторское предложение'),
 ('employment_city', ['geography', 'occupation', 'organization'], True, 'Контакт и конкретизированный профессиональный профиль; авторское предложение'),
 ('split', ['age', 'sex', 'geography'], False, 'Контакт и жёлтые сведения принадлежат разным людям'),
]
YELLOW_BUNDLES = [
 ('first_age', ['age'], False, 'Имя и возраст без контакта не достаточны в авторской гипотезе'),
 ('demographic', ['age', 'sex'], False, 'Широкий демографический профиль без дополнительного основания'),
 ('demographic_city', ['age', 'sex', 'geography'], False, 'Имя, возраст, пол и город без более узкого контекста'),
 ('demographic_job', ['age', 'sex', 'geography', 'occupation'], False, 'Профессия без точного работодателя: пограничное авторское отрицательное решение'),
 ('rich_employment', ['age', 'sex', 'geography', 'occupation', 'organization'], True, 'Подробный именной профессиональный профиль с работодателем; авторское предложение'),
 ('employment', ['occupation', 'organization'], True, 'Имя, профессия и точный работодатель; спорное авторское положительное предложение'),
 ('split', ['age', 'sex', 'geography', 'occupation', 'organization'], False, 'Профессиональные и демографические сведения распределены между разными людьми'),
 ('repeated', ['age', 'sex'], False, 'Повторы имени, возраста и пола не добавляют новых признаков'),
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def profiles(index):
    result = {}
    for role, person in enumerate('abcs'):
        row = synthetic_profile(index + 1000, person)
        first = FIRST_NAMES[(index + role * 3) % len(FIRST_NAMES)]
        feminine = first in {'Вера', 'Нина', 'Лада', 'Алина', 'Кира', 'Полина'}
        last = 'Макетонова' if feminine else 'Макетонов'
        number = 24 + (index * 3 + role * 7) % 36
        unit = 'лет' if 11 <= number % 100 <= 14 or number % 10 not in {1, 2, 3, 4} else 'год' if number % 10 == 1 else 'года'
        city = CITIES[(index + role) % len(CITIES)]
        row.update(first=first, last=last, age=f'{number} {unit}', sex='женский' if feminine else 'мужской',
                   geography=city, occupation=JOBS[(index + role) % len(JOBS)],
                   organization=EMPLOYERS[(index + role) % len(EMPLOYERS)])
        row['address'] = row['address'].replace('Пробоград', city)
        result[person] = row
    result['ab'] = result['a']
    return result


def materialize(template, values, mid, count):
    text, entities, service, offset = '', [], [], 0
    for match in SLOT.finditer(template):
        owner, field = match.groups()
        text += template[offset:match.start()]
        value = values[owner][field]
        start = len(text); text += value
        person = None if owner == 's' else list(owner) if len(owner) > 1 else owner
        entity = dict(id=f'e{count+len(entities)+1}', message_id=mid, start=start, end=len(text),
                      type=TYPES[field], person_id=person)
        entities.append(entity)
        if owner == 's':
            service.append(entity['id'])
        offset = match.end()
    text += template[offset:]
    if '{' in text or '}' in text:
        raise ValueError('Неизвестный слот разметки')
    return dict(id=mid, text=text), entities, service


def plan(family, contact, bundle, fields, positive):
    """Момент достижения авторского основания задаётся в исходном сюжете."""
    rows = []
    if family == 'orange_yellow':
        rows.append((CONTACT[contact], False))
        for field in fields:
            template = FIELD[field]
            if bundle == 'split':
                template = {'age': 'Возраст второго участника — {b:age}.',
                            'sex': 'Пол в анкете второго участника — {b:sex}.',
                            'geography': 'Город третьего участника — {c:geography}.'}[field]
            rows.append((template, positive and field == fields[-1]))
        if bundle == 'first_age':
            rows.append(('Повторю мой возраст: {a:age}. Это то же значение, а не новая запись.', False))
    elif family == 'yellow_only':
        for field in fields:
            if bundle == 'split':
                who = 'b' if field in {'age', 'sex', 'geography'} else 'c'
                label = 'второго' if who == 'b' else 'третьего'
                template = dict(age=f'Возраст {label} участника — {{{who}:age}}.',
                    sex=f'Пол в анкете {label} участника — {{{who}:sex}}.',
                    geography=f'Город {label} участника — {{{who}:geography}}.',
                    occupation=f'По профессии третий участник работает {{{who}:occupation}}.',
                    organization=f'Работодатель третьего участника — {{{who}:organization}}.')[field]
            else:
                template = FIELD[field]
            rows.append((template, positive and field == fields[-1]))
        if bundle == 'repeated':
            rows.extend([('Ещё раз мои данные: имя {a:first}, возраст {a:age}, пол {a:sex}.', False),
                         ('Проверьте повтор: {a:first}, {a:age}. Новых сведений я не добавляю.', False)])
    elif bundle == 'phone_email':
        rows = [(CONTACT['phone'], False), ('Личный телефон второго участника {b:phone}. Это отдельная запись.', False),
                (CONTACT['email'], True)]
    elif bundle == 'name_contact':
        rows = [('Моя фамилия {a:last}. В анкете она должна стоять рядом с моим именем.', False),
                ('Пока проверяю только свою анкету. Данные второй записи менять не прошу.', False),
                (CONTACT['phone'], True)]
    elif bundle == 'shared_employment':
        rows = [('Наш общий личный телефон {ab:phone}. Мы оба им пользуемся.', False),
                (FIELD['occupation'], False), (FIELD['organization'], True)]
    elif bundle == 'service_demographic':
        rows = [('В памятке служебный телефон поддержки {s:phone}. Это не наш личный номер.', False),
                (FIELD['age'], False), (FIELD['sex'], False), (FIELD['geography'], False)]
    return rows


def build(output, seed=20261126):
    if output.exists():
        raise ValueError('Выходной каталог должен быть новым')
    rng = random.Random(seed)
    specifications = []
    for contact in ['phone', 'email', 'address']:
        specifications.extend(('orange_yellow', contact, *bundle) for bundle in ORANGE_BUNDLES)
    specifications.extend(('yellow_only', None, *bundle) for bundle in YELLOW_BUNDLES)
    specifications.extend(('control', None, key, [], positive, rationale) for key, positive, rationale in [
        ('phone_email', True, 'Принятое качественное сочетание личного телефона и email'),
        ('name_contact', True, 'Принятое качественное сочетание частей имени и личного контакта'),
        ('shared_employment', True, 'Общий контакт и профессиональные сведения одного владельца; авторское предложение'),
        ('service_demographic', False, 'Служебный телефон не является личным контактом участника')])
    assert len(specifications) == 30
    jobs = [(spec, variant) for variant in range(10) for spec in specifications]
    rng.shuffle(jobs)
    records, review, lengths, buckets = [], [], Counter(), Counter()
    for index, (spec, variant) in enumerate(jobs, 1):
        family, contact, bundle, fields, positive, rationale = spec
        values = profiles(index)
        people = 3 if bundle == 'split' or variant % 2 else 2
        intro = 'Меня зовут {a:first}. ' + CONTEXTS[(index + variant) % len(CONTEXTS)]
        intro += ' Второй участник — {b:first}.'
        if people == 3:
            intro += ' Третий участник — {c:first}.'
        rows = [(intro, False)] + plan(family, contact, bundle, fields, positive)
        target = min(10, max(len(rows), 5 + variant % 6))
        for extra in rng.sample(EXTRAS, target - len(rows)):
            rows.insert(rng.randrange(1, len(rows) + 1), (extra, False))
        sid = f'weight-300-{index:04d}'
        messages, entities, services, ledger, first = [], [], [], [], None
        for n, (template, trigger) in enumerate(rows, 1):
            message, spans, service = materialize(template, values, f'm{n}', len(entities))
            messages.append(message); entities.extend(spans); services.extend(service)
            if trigger:
                first = n
            ledger.append(dict(message_id=f'm{n}', expected_first_alert=first,
                               basis='independent_authored_proposal_not_runtime_weights', rationale=rationale))
        assert (first is not None) == positive
        doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages,
                   entities=entities, case_flags=FLAGS.copy())
        validate(doc)
        record = dict(session_id=sid, family=f'{family}_{contact or "none"}_{bundle}',
            bucket=family, combination=bundle, orange_type=contact, people=people, variant=variant,
            gold=doc, expected_first_alert=first, author_alert_rationale=rationale,
            alert_label_status='existing_combination_rule' if bundle in {'phone_email', 'name_contact'} else 'author_proposal_pending_review',
            gold_service_contacts=services, author_prefix_ledger=ledger)
        records.append(record); lengths[len(messages)] += 1; buckets[family] += 1
        texts = {m['id']: m['text'] for m in messages}
        review.extend([f'## {sid} — {record["family"]}', '', *[f"**{m['id']}** {m['text']}\n" for m in messages],
            '| Сообщение | Фрагмент | Тип | Владельцы |', '|---|---|---|---|'])
        review.extend(f"| {e['message_id']} | {texts[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |" for e in entities)
        review.extend([f'Авторский первый алерт: {first}. Статус: {record["alert_label_status"]}.', rationale, ''])
    manifest = dict(version=VERSION, synthetic_only=True, message_selection='user_only', seed=seed,
        sessions=len(records), messages=sum(len(r['gold']['messages']) for r in records),
        entities=sum(len(r['gold']['entities']) for r in records), lengths=dict(lengths), buckets=dict(buckets),
        positive_sessions=sum(r['expected_first_alert'] is not None for r in records),
        annotation_source='assistant_authored_explicit_spans_owners_and_provisional_alert_labels',
        human_review_status='pending', service_weights_used_for_gold=False,
        label_policy_version=POLICY, label_policy_status='proposed_not_accepted_team_policy',
        evaluation_note_ru='Метки алертов основаны на предварительной авторской гипотезе. Это согласие с авторской разметкой, а не подтверждение политики ТЗ или реального риска. Нужны ревью и независимая проверка.',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        profile_source_sha256=hashlib.sha256((ROOT/'tools/build_complex_200.py').read_bytes()).hexdigest(),
        limitations=['30 authored combination families with 10 variants each', 'synthetic profiles; natural given names are not actual people',
                     'provisional alert judgments need review; not evidence of real identifiability',
                     'no service weights, NER or linker predictions used to produce gold labels'])
    output.mkdir(parents=True)
    write(output/'snapshot.json', dict(**manifest, records=records))
    write(output/'gold.json', dict(annotations=[r['gold'] for r in records]))
    write(output/'sessions.json', dict(synthetic_only=True, sessions=[dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in records]))
    write(output/'manifest.json', manifest)
    write(output/'author-label-policy.json', dict(version=POLICY, status=manifest['label_policy_status'],
        orange_bundles=ORANGE_BUNDLES, yellow_bundles=YELLOW_BUNDLES,
        note='Положительные решения о профессиональных профилях — авторское предложение для ревью, без доказательства уникальности и без текущих весов. Принятые инструкции не меняются.'))
    (output/'sessions-for-review.md').write_text('\n'.join(review), encoding='utf-8')
    (output/'generation-prompt.md').write_text('# Авторское задание\n\n300 синтетических сессий по 5–10 сообщений пользователя, 2–3 человека. '
        '180 сочетаний одного оранжевого с жёлтыми, 80 только жёлтых, 40 контролей. Варьировать контакт, порядок раскрытия, '
        'профили, общую принадлежность, повторы и раздельных владельцев. Разметить исходные спаны и владельцев явно. '
        'Алерты задать отдельной авторской гипотезой до запуска сервиса, не использовать его веса. Спорные решения сохранять '
        'как предложения для ревью. Обычные имена входят только в вымышленные профили; контакты нерабочие.\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'data/experiments/weight-combinations-300-2026-11-25')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Данные должны оставаться в data/')
    print(json.dumps(build(args.output.resolve()), ensure_ascii=False))
