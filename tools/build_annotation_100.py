"""Сотня синтетических пользовательских сессий: 20 семейств по 5 вариантов."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from annotation_calculator import Calculator
from annotation_contract import assess
from run_compatible_annotation_pilot import materialize_quotes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'data/experiments/annotation-100-2026-11-19'


def cases(v):
    """Явные авторские спаны; никакой модели или исходных персональных данных."""
    name = ['Тестимир', 'Макетослав', 'Образимир', 'Тестослав', 'Макетимир'][v]
    surname = ['Макетонов', 'Примеронов', 'Синтетиков', 'Образцовский', 'Тестоградов'][v]
    patronymic = ['Макетович', 'Тестимирович', 'Образимирович', 'Макетославович', 'Тестославович'][v]
    phone = f'+7 (000) 000-00-{40+v}'
    email = f'person{v}@example.invalid'
    address = f'г. Макетоград, ул. Примерная, д. {10+v}, кв. {20+v}'
    intro = ['Здравствуйте!', 'Добрый день!', 'Подскажите, пожалуйста.', 'Нужна помощь.', 'Хочу уточнить.'][v]
    # span: номер сообщения, точная строка, класс, персона, номер точного вхождения.
    def e(m, q, t, p='p1', occurrence=1):
        return (m, q, t, p, occurrence)
    return [
        ('clean', [f'{intro} Как отключить уведомления о публикациях?',
                   'Нашёл настройку. Теперь оставлю только важные уведомления.'], [], None),
        ('generic_occupation', [f'{intro} Хочу обратиться к психологу. Как выбрать специалиста?',
                                'Нужна ещё консультация юриста по договору.'],
         [e(1,'психологу','occupation',None),e(2,'юриста','occupation',None)], None),
        ('employment', [f'{intro} Я работаю бухгалтером.',
                        f'Мой работодатель — ООО «Макет-{v}».'],
         [e(1,'бухгалтером','occupation'),e(2,f'ООО «Макет-{v}»','organization')], None),
        ('literature', [f'{intro} Читаю рассказ. Персонажа зовут {name}, фамилия {surname}.',
                        'Обсуждаем поступки персонажа, а не мою биографию.'],
         [e(1,name,'first_name','p_character'),e(1,surname,'last_name','p_character')], None),
        ('conversational_repeat', [f'{intro} На встречу записались Соня и Тёмка.',
                                   'Передал Соне записку. Соня подтвердила, что Соня придёт.'],
         [e(1,'Соня','first_name','p_sonya'),e(1,'Тёмка','first_name','p_temka'),
          e(2,'Соне','first_name','p_sonya'),e(2,'Соня','first_name','p_sonya'),
          e(2,'Соня','first_name','p_sonya',2)], None),
        ('possessive', [f'{intro} Сонины вещи лежат в коробке, Тёмкина тетрадь рядом.',
                        'На коробке только инициалы С. Т.; других подписей нет.'], [], None),
        ('fi_email', [f'{intro} Я {surname} {name}. Хочу изменить запись.',
                      f'Моя личная почта {email}.'],
         [e(1,surname,'last_name'),e(1,name,'first_name'),e(2,email,'email')], ('m2','combination_rule')),
        ('io_phone', [f'{intro} Меня зовут {name}, моё отчество {patronymic}.',
                       f'Мой личный телефон {phone}.'],
         [e(1,name,'first_name'),e(1,patronymic,'patronymic'),e(2,phone,'phone')], ('m2','combination_rule')),
        ('fo_address', [f'{intro} Моя фамилия {surname}, отчество {patronymic}.',
                         f'Я живу по адресу {address}.'],
         [e(1,surname,'last_name'),e(1,patronymic,'patronymic'),e(2,address,'address')], ('m2','combination_rule')),
        ('fio_only', [f'{intro} Я {surname} {name} {patronymic}.',
                       'Хочу уточнить порядок записи на консультацию.'],
         [e(1,surname,'last_name'),e(1,name,'first_name'),e(1,patronymic,'patronymic')], None),
        ('different_people', [f'{intro} Заявитель: {surname} {name}.',
                               f'А я другой человек, мой личный email {email}.'],
         [e(1,surname,'last_name','p_applicant'),e(1,name,'first_name','p_applicant'),
          e(2,email,'email','p_speaker')], None),
        ('service_contacts', [f'{intro} Я {surname} {name}.',
                              f'Общий телефон справочной {phone}, общий email поддержки {email}. Это не мои контакты.'],
         [e(1,surname,'last_name'),e(1,name,'first_name'),e(2,phone,'phone',None),e(2,email,'email',None)], None),
        ('late_owner', [f'{intro} В заметке email {email}. Пока не знаю, чей он.',
                        f'Выяснил, что та почта моя. Я {surname} {name}.',
                        f'Мой личный телефон {phone}.'],
         [e(1,email,'email',None),e(2,surname,'last_name'),e(2,name,'first_name'),e(3,phone,'phone')], ('m3','combination_rule')),
        ('correction', [f'{intro} Я {surname} {name}.', f'Я живу по адресу {address}.',
                        f'Исправляю свой адрес проживания: {address.replace(str(20+v),str(70+v))}.'],
         [e(1,surname,'last_name'),e(1,name,'first_name'),e(2,address,'address'),
          e(3,address.replace(str(20+v),str(70+v)),'address')], ('m2','combination_rule')),
        ('card', [f'{intro} Хочу вернуть оплату.', 'Мой полный номер банковской карты: 0000 0000 0000 0000.'],
         [e(2,'0000 0000 0000 0000','bank_card')], ('m2','red_rule')),
        ('passport', [f'{intro} Нужна помощь с заявлением.', 'Мой паспорт: серия и номер 0000 000000.'],
         [e(2,'0000 000000','passport')], ('m2','red_rule')),
        ('snils', [f'{intro} Не проходит проверка анкеты.', 'Мой СНИЛС: 000-000-000 00.'],
         [e(2,'000-000-000 00','snils')], ('m2','red_rule')),
        ('inn', [f'{intro} Это учебный вымышленный кейс для проверки анкеты.', 'ИНН физического лица: 000000000000.'],
         [e(2,'000000000000','individual_inn',None)], ('m2','red_rule')),
        ('yellow_demographics', [f'{intro} Мне {30+v} лет, мой пол — мужской.', 'Я живу в Москве.'],
         [e(1,f'{30+v} лет','age'),e(1,'мужской','sex'),e(2,'Москве','geography')], None),
        ('words_masks', [f'{intro} Мне тридцать пять лет. Телефон записан словами: плюс семь, ноль, ноль.',
                         'СНИЛС словами: ноль ноль ноль. Email скрыт: ***@***. Подпись: Т. М.'], [], None),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        parser.error('Нужен новый каталог внутри data/')
    calculator = Calculator()
    pool = [(v,case) for v in range(5) for case in cases(v)]
    random.Random(20261119).shuffle(pool)
    sessions, gold, checks, prefixes, families = [], [], [], [], Counter()
    lines = ['# Сто синтетических пользовательских сессий', '',
             '20 семейств по 5 вариантов. Это контролируемая проверка правил, не 100 независимых естественных диалогов.',
             'Все контакты, документы, адреса и имена вымышлены; числовые документы — нулевые суррогаты.',
             'Авторский эталон подготовлен текущей LLM до запуска Gemma; проверка человеком ожидается.', '']
    for i,(variant,(family,texts,spans,expected)) in enumerate(pool,1):
        source = dict(session_id=f'synthetic-{i:03d}',messages=[dict(id=f'm{n}',text=t) for n,t in enumerate(texts,1)])
        flags = dict(is_example=family=='inn',annotation_uncertain=False,clarification_reason=None)
        raw = dict(**source,schema_version='annotation-quotes-v1',case_flags=flags,
                   entities=[dict(id=f'e{j}',message_id=f'm{m}',mention=q,type=t,person_id=p,occurrence=o)
                             for j,(m,q,t,p,o) in enumerate(spans,1)])
        doc = materialize_quotes(raw,source)
        result = assess(doc,calculator)
        first = result['first_alert']
        actual = (first['message_id'],first['reason']) if first else None
        if actual != expected:
            raise ValueError(f'{family}: expected {expected}, got {actual}')
        doc['case_flags'] = result['case_flags']
        for n in range(1,len(texts)+1):
            prefix = {**doc,'messages':doc['messages'][:n],
                      'entities':[e for e in doc['entities'] if int(e['message_id'][1:])<=n], 'case_flags':flags}
            prefixes.append(dict(session_id=source['session_id'],prefix_length=n,assessment=assess(prefix,calculator)))
        sessions.append(source)
        gold.append(doc)
        checks.append(dict(session_id=source['session_id'],family=family,variant=variant,assessment=result))
        families[family] += 1
        lines += [f"## {source['session_id']} — {family}", '']
        lines += [f'**m{n}:** {t}\n' for n,t in enumerate(texts,1)]
        lines += [f'Авторский первый алерт: {actual}.', '', '| Сообщение | Фрагмент | Тип | Персона | Вхождение |', '|---|---|---|---|---|']
        lines += [f'| m{m} | {q} | {t} | {p or "null"} | {o} |' for m,q,t,p,o in spans]
        lines += ['']
    output.mkdir(parents=True)
    manifest = dict(message_selection='user_only',synthetic_only=True,seed=20261119,
                    families=dict(families),sessions=len(sessions),messages=sum(len(s['messages']) for s in sessions),
                    annotation_source='current_interactive_llm_session',human_review_status='pending',
                    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    policy_version=calculator.policy['version'],scope='controlled templates; not natural dialogue evaluation')
    for name,value in [('sessions.json',dict(synthetic_only=True,sessions=sessions)),('gold.json',dict(annotations=gold)),
                       ('gold-assessments.json',dict(checks=checks)),('gold-prefixes.json',dict(steps=prefixes)),('manifest.json',manifest)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'sessions-for-review.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(sessions=len(sessions),messages=manifest['messages'],entities=sum(len(d['entities']) for d in gold),
                          positive_sessions=sum(c['assessment']['session_alert'] for c in checks)),ensure_ascii=False))


if __name__ == '__main__':
    main()
