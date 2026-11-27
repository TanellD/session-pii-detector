"""Авторский синтетический корпус: 40 семейств по 25 вариантов, ownership-v2."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from annotation_calculator import Calculator
from annotation_contract import assess, validate
from build_annotation_100 import cases
from run_compatible_annotation_pilot import materialize_quotes

ROOT = Path(__file__).resolve().parents[1]
FLAGS = dict(is_example=False, annotation_uncertain=False, clarification_reason=None)


def additional_cases(v):
    name = ['Тестимир', 'Макетослав', 'Образимир', 'Тестослав', 'Макетимир'][v % 5]
    surname = ['Макетонов', 'Примеронов', 'Синтетиков', 'Образцовский', 'Тестоградов'][v % 5]
    other = 'Шаблоний'
    phone = f'+7 (000) 000-{v // 100:02d}-{v % 100:02d}'
    email = f'owner{v}@example.invalid'
    addr = f'г. Макетоград, ул. Синтетическая, д. {v + 10}, кв. {v + 30}'
    def e(m, q, t, p='self', occurrence=1):
        return m, q, t, p, occurrence
    return [
        ('shared_phone_personal_email', [f'Меня зовут {name}, а коллегу — {other}.',
            f'Наш общий личный телефон {phone}. Им пользуемся мы оба.', f'А это только моя личная почта {email}.'],
            [e(1,name,'first_name'),e(1,other,'first_name','colleague'),e(2,phone,'phone',['self','colleague']),e(3,email,'email')], ('m3','combination_rule')),
        ('shared_phone_repeat', [f'Я {name}. Второй владелец — {other}, мой коллега.',
            f'Телефон {phone} принадлежит нам обоим.', f'Повторяю наш общий личный телефон: {phone}.'],
            [e(1,name,'first_name'),e(1,other,'first_name','colleague'),e(2,phone,'phone',['self','colleague']),e(3,phone,'phone',['self','colleague'])], None),
        ('shared_address', [f'Я {surname} {name}. Мой сосед — {other}.',
            f'Мы оба постоянно живём по адресу {addr}.'],
            [e(1,surname,'last_name'),e(1,name,'first_name'),e(1,other,'first_name','neighbor'),e(2,addr,'address',['self','neighbor'])], ('m2','combination_rule')),
        ('unknown_contact_pair', [f'В заметке записана почта {email}. Не знаю, чья она.',
            f'Там же телефон {phone}. Его владелец тоже неизвестен.'],
            [e(1,email,'email',None),e(2,phone,'phone',None)], None),
        ('late_owner_remention', [f'Нашёл почту {email}, пока не знаю владельца.',
            f'Выяснил: это моя личная почта. Меня зовут {name}, фамилия {surname}.',
            f'Повторю явно: моя личная почта {email}.'],
            [e(1,email,'email',None),e(2,name,'first_name'),e(2,surname,'last_name'),e(3,email,'email')], ('m3','combination_rule')),
        ('split_fio_messages', [f'Меня зовут {name}.', f'Моя фамилия {surname}.',
            'Моё отчество Шаблонович.', f'Мой личный email {email}.'],
            [e(1,name,'first_name'),e(2,surname,'last_name'),e(3,'Шаблонович','patronymic'),e(4,email,'email')], ('m4','combination_rule')),
        ('explicit_surname_context', [f'Меня зовут {name}, фамилия {surname}.', f'Моя личная почта {email}.'],
            [e(1,name,'first_name'),e(1,surname,'last_name'),e(2,email,'email')], ('m2','combination_rule')),
        ('organization_contains_surname', [f'Мой работодатель ООО «{surname}».',
            'В названии фирмы нет сведений о моей фамилии.'], [e(1,f'ООО «{surname}»','organization')], None),
        ('email_contains_name', [f'Моя личная почта testimir.{v}@example.invalid.',
            'Имя и фамилию отдельно я пока не сообщал.'], [e(1,f'testimir.{v}@example.invalid','email')], None),
        ('phone_invalid', ['В форме стоит телефон abc12.', 'Вторая запись: +7 (000 000-00-00. Она тоже с ошибкой.'], [], None),
        ('phone_short_extension', ['В поле только короткий номер 12-34.', 'Добавочный 123. Полного телефона нет.'], [], None),
        ('phone_two_formats', [f'Мой личный телефон {phone}.', f'Тот же мой телефон: 8000000{v:04d}.'],
            [e(1,phone,'phone'),e(2,f'8000000{v:04d}','phone')], None),
        ('invalid_inn_checksum', ['Мой ИНН: 000000000001.', 'Это ошибка контрольной суммы, не исправляйте цифры.'], [], None),
        ('invalid_snils_checksum', ['Мой СНИЛС: 000-000-000 01.', 'Прошу проверить ввод, не подставлять другой номер.'], [], None),
        ('invalid_card_checksum', ['Моя карта: 0000 0000 0000 0001.', 'Возможно, последняя цифра введена неверно.'], [], None),
        ('person_contact', ['Контакт подписан псевдонимом «МакетныйСтранник».',
            f'Личная почта этого человека {email}.'],
            [e(1,'МакетныйСтранник','person','contact'),e(2,email,'email','contact')], None),
        ('two_people_contact_pair', [f'Я {name}, коллега — {other}.',
            f'Моя личная почта {email}. Личный телефон коллеги {phone}.'],
            [e(1,name,'first_name'),e(1,other,'first_name','colleague'),e(2,email,'email'),e(2,phone,'phone','colleague')], None),
        ('same_owner_contact_pair', [f'Моя личная почта {email}.', f'Мой личный телефон {phone}.'],
            [e(1,email,'email'),e(2,phone,'phone')], ('m2','combination_rule')),
        ('excluded_categories', ['Хочу записать дату рождения 01.01.2000.',
            'Ещё в анкете есть поля: родство, религия, диагноз. Значения не указываю.'], [], None),
        ('address_alone', [f'Я живу по адресу {addr}.', 'Других личных сведений для заявки пока не сообщаю.'],
            [e(1,addr,'address')], None),
    ]


def variants(v):
    base = v % 5
    old_phone = f'+7 (000) 000-00-{40 + base}'
    new_phone = f'+7 (000) 000-01-{v:02d}'
    for family, texts, spans, expected in cases(base):
        replacements = [(old_phone,new_phone),(f'person{base}@example.invalid',f'person{v}@example.invalid'),
                        (f'ООО «Макет-{base}»',f'ООО «Макет-{v}»')]
        def transform(value):
            for old, new in replacements:
                value = value.replace(old,new)
            return value
        yield family, list(map(transform,texts)), [(m,transform(q),t,p,o) for m,q,t,p,o in spans], expected
    yield from additional_cases(v)


def build(output, seed=20261123):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'data'):
        raise ValueError('Нужен новый каталог внутри data/')
    calculator = Calculator()
    pool = [(v,row) for v in range(25) for row in variants(v)]
    random.Random(seed).shuffle(pool)
    sessions, annotations, checks, prefixes = [], [], [], []
    families = Counter()
    review = ['# Авторская разметка синтетических сессий', '',
              'Контролируемые семейства; не независимые естественные диалоги. Проверка человеком ожидается.', '']
    for index,(variant,(family,texts,spans,expected)) in enumerate(pool,1):
        texts = list(texts)
        spans = list(spans)
        if variant % 3 == 0:
            texts.insert(0, ['Нужна помощь с заявкой.', 'Подскажите, как изменить запись.', 'Не получается заполнить форму.'][variant % 3])
            spans = [(m+1,q,t,p,o) for m,q,t,p,o in spans]
            if expected:
                expected = (f'm{int(expected[0][1:])+1}',expected[1])
        elif variant % 3 == 1:
            texts.append('Спасибо, пока это все сведения для заявки.')
        # Уникальный нейтральный идентификатор формы не является ПДн.
        texts[0] = f'Заявка TEST-{index:04d}. ' + texts[0]
        source = dict(session_id=f'synthetic-1000-{index:04d}',messages=[dict(id=f'm{n}',text=t) for n,t in enumerate(texts,1)])
        flags = {**FLAGS,'is_example':family=='inn'}
        raw = dict(**source,schema_version='annotation-quotes-v1',case_flags=flags,
                   entities=[dict(id=f'e{j}',message_id=f'm{m}',mention=q,type=t,person_id=p,occurrence=o)
                             for j,(m,q,t,p,o) in enumerate(spans,1)])
        doc = materialize_quotes(raw,source)
        validate(doc)
        result = assess(doc,calculator)
        first = result['first_alert']
        actual = (first['message_id'],first['reason']) if first else None
        if actual != expected:
            raise ValueError(f'{family}/{variant}: author expected {expected}, got {actual}')
        doc['case_flags'] = result['case_flags']
        for length in range(1,len(texts)+1):
            prefix = {**doc,'messages':doc['messages'][:length],
                      'entities':[e for e in doc['entities'] if int(e['message_id'][1:])<=length], 'case_flags':flags}
            prefixes.append(dict(session_id=source['session_id'],prefix_length=length,assessment=assess(prefix,calculator)))
        sessions.append(source)
        annotations.append(doc)
        checks.append(dict(session_id=source['session_id'],family=family,variant=variant,
                           author_expected_first_alert=expected,assessment=result))
        families[family] += 1
        review += [f"## {source['session_id']} — {family}", '']
        review += [f"**{m['id']}:** {m['text']}\n" for m in source['messages']]
        review += ['| Сообщение | Фрагмент | Тип | Владельцы |','|---|---|---|---|']
        messages = {m['id']:m['text'] for m in source['messages']}
        review += [f"| {e['message_id']} | {messages[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |" for e in doc['entities']]
        review += [f'Авторское ожидание первого алерта: {expected}.', '']
    manifest = dict(version='annotation-1000-ownership-v2-2026-11-23',message_selection='user_only',
        synthetic_only=True,seed=seed,families=dict(sorted(families.items())),sessions=len(sessions),
        messages=sum(len(s['messages']) for s in sessions),entities=sum(len(d['entities']) for d in annotations),
        positive_sessions=sum(c['assessment']['session_alert'] for c in checks),
        annotation_source='interactive_assistant_authored_scenario_ledger_before_external_model',
        human_review_status='pending',policy_version=calculator.policy['version'],
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        base_generator_sha256=hashlib.sha256((ROOT/'tools/build_annotation_100.py').read_bytes()).hexdigest(),
        scope='controlled templates; variants are dependent; group by family to avoid train/test leakage',
        document_checksum='extraction_filter',service_weights_used_for_gold=False)
    output.mkdir(parents=True)
    for filename,value in [('sessions.json',dict(synthetic_only=True,sessions=sessions)),
            ('gold.json',dict(annotations=annotations)),('gold-assessments.json',dict(checks=checks)),
            ('gold-prefixes.json',dict(steps=prefixes)),('manifest.json',manifest)]:
        (output/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'sessions-for-review.md').write_text('\n'.join(review),encoding='utf-8')
    (output/'generation-prompt.md').write_text(
        '# Задание генератору\n\nТолько синтетические пользовательские сообщения на русском. '
        'Сценарии и авторская разметка явно заданы в tools/build_annotation_1000.py; внешняя модель не генерирует тексты. '
        'Включить обычное накопление, разных и совместных владельцев, null без новой персоны, '
        'позднее уточнение без изменения прошлого, повторы, исправления, служебные контакты, '
        'неправильные checksum и телефоны, вложенные имена в организации/email, PERSON и исключённые классы. '
        'Составить точные непересекающиеся спаны и владельцев до внешней разметки. '
        'Первый алерт задать по ТЗ, без весов сервиса. Контакты только с кодом 000 и example.invalid, '
        'документы — нулевые синтетические суррогаты. Не отправлять авторскую разметку проверяющей модели.\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output),ensure_ascii=False))
