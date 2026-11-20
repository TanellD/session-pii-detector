"""Авторские синтетические пользовательские сессии и эталон без будущей перелинковки."""
import argparse
import hashlib
import json
from pathlib import Path

from annotation_calculator import Calculator
from annotation_contract import assess
from run_compatible_annotation_pilot import materialize_quotes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'data/experiments/user-only-sessions-2026-11-19'

# Шаблон, пользовательские сообщения, авторские спаны, ожидаемый алерт.
CASES = [
    ('clean', 'Помощь с настройкой уведомлений, без личных данных', [
        'Добрый день! Хочу отключить уведомления о новых публикациях, но оставить ответы на мои вопросы. Это можно настроить отдельно?',
        'Нашёл переключатель. Спасибо, теперь сообщения не мешают работать.'
    ], [], None),
    ('delivery', 'Доставка: представление, адрес, исправление квартиры', [
        'Здравствуйте! Не получается изменить доставку в приложении. Заказ оформлял я, Макетонов Тестослав. Поможете поменять адрес?',
        'Нужно доставить ко мне домой: г. Макетоград, ул. Примерная, д. 6, кв. 12. После обеда я буду дома.',
        'Ошибся квартирой в прошлом сообщении. Я живу по адресу г. Макетоград, ул. Примерная, д. 6, кв. 21. Вот этот адрес правильный.'
    ], [(1,'Макетонов','last_name','p1'), (1,'Тестослав','first_name','p1'),
        (2,'г. Макетоград, ул. Примерная, д. 6, кв. 12','address','p1'),
        (3,'г. Макетоград, ул. Примерная, д. 6, кв. 21','address','p1')], ('m2','combination_rule')),
    ('booking', 'Запись: имя, отчество и личная почта появляются постепенно', [
        'Хочу записаться на консультацию на следующую неделю. Меня зовут Образимир, в будни мне удобнее вечером.',
        'Для записи добавлю: моё отчество Макетович. Оплатить смогу после подтверждения времени.',
        'Подтверждение отправьте на мою личную почту obrazimir.booking@example.invalid, пожалуйста. На работе я редко беру телефон.'
    ], [(1,'Образимир','first_name','p1'), (2,'Макетович','patronymic','p1'),
        (3,'obrazimir.booking@example.invalid','email','p1')], ('m3','combination_rule')),
    ('late_owner', 'Позднее раскрытие владельца не меняет прежний неизвестный спан', [
        'Разбираю старые заметки по заявке. Там записано archive.contact@example.invalid, но пока не понимаю, чей это адрес почты.',
        'Проверил, та почта из заметки моя. Я Примеронов Тестимир, заявку тоже подавал я. Можно продолжить здесь?',
        'Если нужно связаться, мой личный телефон +7 (000) 000-00-21. Почту ещё не успел проверить.'
    ], [(1,'archive.contact@example.invalid','email',None), (2,'Примеронов','last_name','p1'),
        (2,'Тестимир','first_name','p1'), (3,'+7 (000) 000-00-21','phone','p1')], ('m3','combination_rule')),
    ('service_contact', 'Общий контакт организации не становится личным контактом автора', [
        'Я Синтетиков Макетимир. Пытаюсь восстановить доступ к личному кабинету, письмо со ссылкой не приходит.',
        'На странице помощи указан телефон справочной +7 (000) 000-00-22 и общий email support@example.invalid. Это контакты службы, не мои. Стоит туда написать?'
    ], [(1,'Синтетиков','last_name','p1'), (1,'Макетимир','first_name','p1'),
        (2,'+7 (000) 000-00-22','phone',None), (2,'support@example.invalid','email',None)], None),
    ('refund', 'Возврат: полный красный маркер после нейтрального вопроса', [
        'Подскажите, как вернуть оплату за отменённую услугу? В приложении вижу только статус отмены.',
        'Оплачивал своей картой, вот её полный номер: 0000 0000 0000 0000. Чек сейчас поищу.'
    ], [(2,'0000 0000 0000 0000','bank_card','p1')], ('m2','red_rule')),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'data'):
        parser.error('Нужен новый каталог внутри data/')
    calculator = Calculator()
    sessions, gold, checks, steps = [], [], [], []
    lines = ['# Пользовательские сессии и авторский эталон', '',
             'Только сообщения пользователя; ответы чатбота отсутствуют. Все данные вымышлены.',
             'Эталон составлен текущей LLM до запуска Gemma. Проверка человеком ожидается.', '']
    for index, (template, title, texts, spans, expected) in enumerate(CASES, 1):
        source = {'session_id': f'user-{index:02d}', 'messages': [{'id': f'm{i}', 'text': t} for i,t in enumerate(texts,1)]}
        raw = {**source, 'schema_version': 'annotation-quotes-v1', 'case_flags': {
            'is_example': False, 'annotation_uncertain': False, 'clarification_reason': None},
            'entities': [dict(id=f'e{i}', message_id=f'm{m}', mention=q, type=t, person_id=p, occurrence=1)
                         for i,(m,q,t,p) in enumerate(spans,1)]}
        doc = materialize_quotes(raw, source)
        result = assess(doc, calculator)
        first = result['first_alert']
        if ((first['message_id'],first['reason']) if first else None) != expected:
            raise ValueError(f'Ожидание автора не совпало с обработчиком: {template}')
        doc['case_flags'] = result['case_flags']
        for n in range(1,len(texts)+1):
            prefix = {**doc, 'messages': doc['messages'][:n],
                      'entities': [e for e in doc['entities'] if int(e['message_id'][1:]) <= n],
                      'case_flags': raw['case_flags']}
            evaluation = assess(prefix,calculator)
            steps.append({'session_id':source['session_id'], 'prefix_length':n, 'assessment':evaluation})
        sessions.append(source)
        gold.append(doc)
        checks.append({'session_id':source['session_id'], 'template':template, 'title':title, 'assessment':result})
        lines += [f"## {source['session_id']}: {title}", '']
        lines += [f'**m{i}, пользователь:** {text}\n' for i,text in enumerate(texts,1)]
        lines += [f"**Первый алерт:** {expected[0] + ' / ' + expected[1] if expected else 'нет'}.", '',
                  '| Сообщение | Фрагмент | Тип | Владелец |', '|---|---|---|---|']
        lines += [f'| m{m} | {q} | {t} | {p or "null"} |' for m,q,t,p in spans] or ['| — | Нет сущностей | — | — |']
        if template == 'late_owner':
            lines += ['', '**Важно:** email из m1 остаётся с null даже после m2. Ранний алерт на m2 означал бы нарушение неизменности прежней принадлежности.']
        lines += ['']
    output.mkdir(parents=True)
    for name,payload in [('sessions.json',{'synthetic_only':True,'sessions':sessions}), ('gold.json',{'annotations':gold}),
                         ('gold-assessments.json',{'checks':checks}), ('gold-prefixes.json',{'steps':steps}),
                         ('manifest.json',{'message_selection':'user_only','synthetic_only':True,
                           'annotation_source':'current_interactive_llm_session','human_review_status':'pending',
                           'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'policy_version':calculator.policy['version']})]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'sessions-for-review.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'sessions':len(sessions),'messages':sum(len(s['messages']) for s in sessions),
        'entities':sum(len(d['entities']) for d in gold),'positive_sessions':sum(c['assessment']['session_alert'] for c in checks),
        'output':str(output)},ensure_ascii=False))


if __name__ == '__main__':
    main()
