"""Авторские сложные диалоги: явная разметка спанов, владельцев и момента алерта."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
from annotation_contract import validate

VERSION = 'complex-multiperson-200-v1'
FLAGS = dict(is_example=False, annotation_uncertain=False, clarification_reason=None)
SLOT = re.compile(r'\{(a|b|c|ab|ac|bc|u|s):(first|last|patronymic|phone|email|address|alias)\}')
TYPES = dict(first='first_name', last='last_name', patronymic='patronymic',
             phone='phone', email='email', address='address', alias='person')
NAMES = ['Тестимир', 'Макетослав', 'Пробомир', 'Демослав', 'Образимир', 'Шабломир',
         'Пробослава', 'Макетина', 'Демолина', 'Тестослава', 'Образина', 'Шаблония']
SURNAMES = ['Пробов', 'Макетонов', 'Образцовский', 'Шаблонов', 'Тестоградов', 'Демонов']
PATRONYMICS = ['Пробович', 'Макетович', 'Образцович', 'Шаблонович', 'Тестимирович', 'Демославович']

# Каждый сюжет содержит пять смысловых реплик. Триггер задан автором по
# качественному основанию ТЗ, не получен из весов или ответа сервиса.
# Предыдущие null-владельцы не переписываются после позднего уточнения.
STORIES = [
 ('repair_shared', 2, 4, 'Личная почта второго заказчика и общий личный телефон', [
  'Здравствуйте. Пишу по ремонту техники для двух квартир. Я {a:first}, второй заказчик — {b:first}. Нужно согласовать один выезд.',
  'Для звонка мне оставлю личный номер {a:phone}. У второго заказчика пока нет доступа к кабинету.',
  'Почта второго заказчика: {b:email}. Пожалуйста, не заменяйте ею контакт моей записи.',
  'Уточняю: номер {ab:phone} общий личный, мы оба им пользуемся. По времени выезда можно связаться с любым из нас.',
  'А смету по моей квартире пришлите мне: моя личная почта {a:email}. Вторую смету отправляйте отдельно.']),
 ('training_namesakes', 2, 2, 'Фамилия и имя с личным телефоном одного владельца', [
  'По записи на курс два человека с одинаковым именем. Первый — {a:first} {a:last}, второй — {b:first} {b:last}. Записи прошу не объединять.',
  'Личный телефон первого участника {a:phone}. Именно по первой записи нужно перенести начало занятий.',
  'Вторая запись пока без телефона. Для второго участника можно использовать личную почту {b:email}.',
  'Первый участник — {a:first} {a:last}, перенос нужен на вечер; у второго расписание остаётся прежним.',
  'Повторю номер первой записи: {a:phone}. Это повтор того же контакта, дополнительную анкету создавать не нужно.']),
 ('late_contact_owner', 3, 5, 'После уточнения владельца телефон и личная почта у одного человека', [
  'Пишу по групповому бронированию. Я {a:first}, ещё участвуют {b:first} и {c:first}. Оплачиваем отдельно.',
  'В старой переписке сохранился номер {u:phone}. Пока не могу подтвердить, кому он принадлежит, не прикрепляйте его к моей записи.',
  'Для моей записи нужна личная почта {a:email}. За третьего участника ничего менять не прошу.',
  'Уточнение по номеру: {b:phone} — личный телефон второго участника, имя в записи {b:first}. Ранее я не знал, чей он.',
  'Теперь есть его личная почта: {b:email}. Подтверждение по второй записи нужно отправить туда.']),
 ('delivery_venue', 3, 5, 'Личный телефон и почта заявителя; место доставки не равно проживанию', [
  'У нас совместная доставка реквизита. Я {a:first}; за второй комплект отвечает {b:first}, за третий — {c:first}.',
  'Доставка на площадку {u:address}. Никто из нас там не живёт, это только место разгрузки.',
  'Для второго комплекта оставлю личную почту второго участника {b:email}. По первому комплекту звоните мне: мой телефон {a:phone}.',
  'Третий участник {c:first} получит свой комплект отдельно. Контакт второго участника к третьей записи не переносите.',
  'Для документов по моему комплекту моя личная почта {a:email}. Адрес площадки оставьте только в доставке.']),
 ('volunteer_shared_email', 3, 5, 'Общая личная почта и личный телефон одного из владельцев', [
  'Записываемся на волонтёрскую смену втроём: я {a:first}, ещё {b:first} и {c:first}. У каждого своя заявка.',
  'У меня и третьего участника общая личная почта {ac:email}; это не адрес организаторов.',
  'У второго участника личный номер {b:phone}. Для него нужна другая смена, не та, которую выбрали мы.',
  'Общая почта относится только к первой и третьей заявкам. Вторую заявку туда не добавляйте.',
  'Для третьей заявки дополнительно личный телефон {c:phone}. Если смену отменят, сначала позвоните по нему.']),
 ('speaker_switch', 3, 5, 'Два личных контакта второго участника после смены пишущего', [
  'Пишу по мастерской. Я {a:first}, ещё записаны {b:first} и {c:first}. Хочу уточнить порядок получения работ.',
  'Мой личный телефон {a:phone}. По двум другим работам сроки могут быть другими.',
  'Теперь сообщение от второго участника, {b:first}: моя личная почта {b:email}. Устройство одно, но заявки разные.',
  'Первый участник {a:first} заберёт работу позже. Третий участник {c:first} пока только уточняет стоимость.',
  'Продолжает второй участник, {b:first}: мой личный телефон {b:phone}. Письмо и звонок по моей работе можно направлять мне.']),
 ('name_parts_apart', 2, 5, 'Полные части имени и личный телефон одного человека', [
  'По двум заявлениям: я {a:first}, второй заявитель — {b:first}. В кабинете не получается открыть вложение.',
  'Для моей анкеты отдельно уточню фамилию: {a:last}. Во второй анкете фамилия другая.',
  'Личная почта второго заявителя {b:email}. Письмо с вложением по второй анкете нужно отправить туда.',
  'Моё отчество {a:patronymic}. Это дополнение к первой анкете, а не данные второго заявителя.',
  'Для моей анкеты личный телефон {a:phone}. Хочу получить уведомление, когда вложение станет доступно.']),
 ('phone_correction', 2, 5, 'Личная почта и телефон после исправления опечатки', [
  'По двум заказам пишу я, {a:first}; второй заказчик — {b:first}. По моему заказу ошибся в одной цифре номера.',
  'Мой правильный личный телефон {a:phone}. Предыдущий ошибочный номер сейчас не повторяю.',
  'Почта второго заказчика {b:email}. Изменение телефона к его заказу не относится.',
  'Проверьте только мою запись: контакт {a:phone}. Это тот же исправленный номер, а не второй телефон.',
  'И моя личная почта {a:email}. Чек по моему заказу удобнее получить письмом.']),
 ('alias_account', 2, 5, 'Личная почта и телефон владельца псевдонима', [
  'По двум аккаунтам: я {a:first}, второй пользователь — {b:first}. У меня профиль подписан псевдонимом «{a:alias}».',
  'Моя личная почта {a:email}. Псевдоним нужен только для поиска профиля, менять подпись не хочу.',
  'Второму пользователю нужна отдельная настройка. Его личный номер {b:phone}.',
  'Мой псевдоним всё ещё «{a:alias}». Почту второго пользователя к моему аккаунту не привязывайте.',
  'Для восстановления моего доступа оставлю свой личный телефон {a:phone}. Если понадобится код, он придёт мне.']),
 ('visit_residence', 3, 4, 'Фамилия и имя с адресом проживания одного участника', [
  'Оформляем три заявки на посещение. Я {a:first} {a:last}; ещё участвуют {b:first} и {c:first}.',
  'Личный номер второго участника {b:phone}. Его заявку нужно проверять отдельно от моей.',
  'Личная почта третьего участника {c:email}. У третьего пока нет подтверждения времени.',
  'Мой адрес проживания {a:address}. Для моей заявки нужен выезд, а двое остальных придут сами.',
  'В моей заявке имя {a:first}, фамилия {a:last}. Напоминание о выезде прошу оставить в кабинете.']),
 ('shared_residence', 2, 5, 'Общий адрес проживания и личный телефон одного жильца', [
  'Я {a:first}, второй жилец — {b:first}. По одной квартире хотим оформить две отдельные заявки на обслуживание.',
  'Наш общий адрес проживания {ab:address}. Живём там оба, но заявки относятся к разным приборам.',
  'По второй заявке пока не оставляем других контактов. Ответ можно показать в кабинете.',
  'Общий адрес относится к обеим заявкам; время обслуживания можно согласовать раздельно.',
  'По моей, первой заявке личный телефон {a:phone}. Второй жилец не пользуется этим номером.']),
 ('service_vs_personal', 3, 5, 'Личные телефон и почта третьего участника; служебные исключены', [
  'Пишу по трём пропускам: я {a:first}, второй участник {b:first}, третий {c:first}. Пропуска оформляем отдельно.',
  'В памятке служебный телефон поддержки {s:phone} и служебная почта {s:email}. Это контакты организации, не наши личные.',
  'Для второго пропуска личный телефон второго участника {b:phone}. По первому отвечайте пока в кабинете.',
  'Для третьего пропуска личная почта третьего участника {c:email}. Не отправляйте документы на почту поддержки.',
  'И личный телефон третьего участника {c:phone}. По третьему пропуску можно и написать, и позвонить.']),
 ('separate_contacts', 2, None, 'Контакты разных людей нельзя складывать в один профиль', [
  'По двум записям на консультацию: я {a:first}, второй участник {b:first}. Время просим подобрать разное.',
  'Мой личный телефон {a:phone}. Второму участнику на него звонить не нужно.',
  'Личная почта второго участника {b:email}. Это не моя почта, у меня личного email в этой переписке нет.',
  'В моей записи только телефон; во второй только почта. Не делайте из двух записей одну.',
  'Номер {a:phone} повторяю для проверки моей записи. Новых данных ко второй записи не добавляю.']),
 ('three_separate', 3, None, 'У каждого человека только один личный контакт или адрес', [
  'На ремонт записываемся втроём: я {a:first}, ещё {b:first} и {c:first}. Три независимые заявки.',
  'По моей заявке личный номер {a:phone}. Адрес проживания я здесь не указываю.',
  'По второй заявке личная почта второго участника {b:email}. Его телефон не знаю.',
  'По третьей заявке адрес проживания третьего участника {c:address}. Других личных контактов по ней сейчас нет.',
  'Проверьте, что телефон остался в первой заявке, почта во второй, адрес в третьей. Общих контактов между нами нет.']),
 ('shared_phone_only', 2, None, 'Один общий личный телефон не создаёт два разных атрибута', [
  'Я {a:first}, ещё записан {b:first}. Хотим перенести две брони, но пока уточняем условия.',
  'Общий личный телефон у нас {ab:phone}. Им пользуемся оба, это одна и та же сим-карта.',
  'Для первой брони повторю общий номер {ab:phone}. Свою почту пока не оставляю.',
  'Для второй брони контакт тот же: {ab:phone}. Другого личного номера не добавляем.',
  'Пока только смотрим доступные варианты. Подтверждение можно оставить прямо в переписке.']),
 ('repeated_phone', 2, None, 'Повторы телефона одного человека и почта другого не образуют пару', [
  'По возвратам: я {a:first}, ещё обращается {b:first}. Возвраты разные, отправления тоже разные.',
  'Мой личный номер {a:phone}. Время для обратного звонка ещё уточню.',
  'Повторю мой телефон {a:phone}. В прошлый раз он записался без пробелов, цифры те же.',
  'Личная почта второго заявителя {b:email}. Чек второго заявителя к моему возврату не прикладывайте.',
  'Для моей заявки всё ещё только номер {a:phone}. Адрес и почту в этом обращении не указываю.']),
 ('unknown_delivery', 3, None, 'Неизвестные контакты нельзя назначать ближайшему участнику', [
  'Обсуждаем общий заказ: я {a:first}, второй участник {b:first}, третий {c:first}. Заказ пока не подтверждён.',
  'На листке есть телефон {u:phone}. Владелец не указан, принадлежность никому из нас не подтверждаю.',
  'Там же почта {u:email}. Не знаю, связана ли она с тем же человеком; не считайте их парой личных контактов.',
  'По своей части могу пока ответить только в этом чате. Не прикрепляйте сведения с листка к моей записи.',
  'Вторая и третья записи тоже пока без личных контактов. Позже каждый сможет указать свои данные отдельно.']),
 ('namesakes_no_contact', 2, None, 'Похожие имена не доказывают одного человека', [
  'Две заявки с одинаковым именем: {a:first} {a:last} и {b:first} {b:last}. Фамилии разные, это разные люди.',
  'Первая заявка — {a:first} {a:last}. По ней хочу только уточнить срок рассмотрения.',
  'Вторая заявка — {b:first} {b:last}. Она создана позже, пожалуйста, не объединяйте историю.',
  'Личные контакты ни по первой, ни по второй заявке в этой переписке пока не передаю.',
  'Если поиск по имени показывает одну строку, проверьте разные фамилии. Оба обращения должны остаться отдельными.']),
 ('email_shared_only', 3, None, 'Общая почта и отдельный телефон третьего человека', [
  'По записи в мастерскую: я {a:first}, второй участник {b:first}, третий {c:first}. У нас разные изделия.',
  'Общая личная почта у меня и второго участника {ab:email}. Третий этой почтой не пользуется.',
  'У третьего участника личный телефон {c:phone}. Прошу не добавлять его к первым двум записям.',
  'Для первых двух записей снова почта {ab:email}. Это повтор одного общего адреса, не второй контакт.',
  'Пока не назначайте единое время выдачи. Для каждой записи позже согласуем его отдельно.']),
 ('service_contacts_only', 3, None, 'Служебные контакты не являются личными атрибутами участников', [
  'По совместной заявке: я {a:first}, ещё участвуют {b:first} и {c:first}. Не можем найти статус проверки.',
  'В справке указан служебный телефон поддержки {s:phone}. Это контакт поддержки, не личный номер кого-либо из нас.',
  'Служебная почта поддержки {s:email}. Её тоже не нужно добавлять в наши личные анкеты.',
  'Каждый из нас посмотрит ответ в своём кабинете. Личных телефонов или почты здесь пока не оставляем.',
  'Проверьте только статус трёх заявок. Контакты из справки относятся к службе поддержки, а не к заявителям.']),
]

EXTRAS = [
 'Уточню по оформлению: можно сохранить изменения как черновик и подтвердить позже?',
 'Пишу с телефона, поэтому сведения отправляю частями. Порядок записей менять не нужно.',
 'Не создавайте новую заявку вместо старой; хочу сохранить историю обращения.',
 'Если стоимость изменится, сначала покажите итог в кабинете, оплату пока не подтверждаю.',
 'Сроки для разных записей могут отличаться. Одновременное выполнение не обязательно.',
 'Приложение иногда теряет соединение. Если сообщение повторится, это не новая заявка.',
 'Вложения пришлю отдельно, когда будет понятно, какой формат поддерживается.',
 'Решение по второй записи не должно автоматически менять первую.',
 'Пока не отменяйте ни одну запись. Сначала хочу проверить условия изменения.',
 'Вопрос ещё актуален. Если требуется отдельное действие в кабинете, укажите его в ответе.',
]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def profile(index, person):
    role = 'abcs'.index(person)
    n = index * 4 + role
    first = NAMES[(index + role * 3) % len(NAMES)]
    feminine = first.endswith(('а', 'я'))
    last = SURNAMES[(index + role) % len(SURNAMES)]
    if feminine:
        last = last[:-2] + 'ая' if last.endswith('ий') else last + 'а'
    patronymic = PATRONYMICS[(index + role) % len(PATRONYMICS)]
    if feminine:
        patronymic = patronymic[:-2] + 'на'
    return dict(first=first, last=last, patronymic=patronymic,
        phone=f'+7 (000) {100+n//100:03d}-{n%100:02d}-{(n*17)%100:02d}',
        email=f'letter{n*37+19}@example.invalid',
        address=f'г. Пробоград, ул. Макетная, д. {10+n%180}, кв. {1+n%70}',
        alias=f'ПробныйПутник{chr(1040+n%32)}')


def materialize(template, profiles, message_id, count):
    text, entities, service = '', [], []
    offset = 0
    for match in SLOT.finditer(template):
        text += template[offset:match.start()]
        owner, slot = match.groups()
        value = profiles[owner][slot]
        start = len(text)
        text += value
        owners = None if owner in {'u', 's'} else list(owner) if len(owner) > 1 else owner
        entity = dict(id=f'e{count+len(entities)+1}', message_id=message_id,
                      type=TYPES[slot], person_id=owners, start=start, end=len(text))
        entities.append(entity)
        if owner == 's':
            service.append(entity['id'])
        offset = match.end()
    text += template[offset:]
    if '{' in text or '}' in text:
        raise ValueError('Неразобранный слот')
    return dict(id=message_id, text=text), entities, service


def build(output, seed=20261125):
    if output.exists():
        raise ValueError('Для новой версии нужен новый каталог')
    rng = random.Random(seed)
    records, review, sessions, plan = [], [], [], []
    length_counts, owners_counts = Counter(), Counter()
    # Перемешивание порядка не меняет содержания и разметки сюжетов.
    jobs = [(story, variant) for variant in range(10) for story in STORIES]
    rng.shuffle(jobs)
    for index, (story, variant) in enumerate(jobs, 1):
        family, people, trigger, rationale, templates = story
        profiles = {p: profile(index, p) for p in 'abcs'}
        if family in {'training_namesakes', 'namesakes_no_contact'}:
            profiles['b']['first'] = profiles['a']['first']
            if profiles['a']['first'].endswith(('а', 'я')):
                last = SURNAMES[(index + 1) % len(SURNAMES)]
                profiles['b']['last'] = last[:-2] + 'ая' if last.endswith('ий') else last + 'а'
            else:
                profiles['b']['last'] = SURNAMES[(index + 1) % len(SURNAMES)]
        profiles['u'] = profiles['b']
        for shared in ['ab', 'ac', 'bc']:
            profiles[shared] = profiles[shared[0]]
        target = 5 + variant % 6
        steps = [(t, n == trigger) for n, t in enumerate(templates, 1)]
        extras = rng.sample(EXTRAS, target - 5)
        for extra in extras:
            steps.insert(rng.randrange(1, len(steps) + 1), (extra, False))
        sid = f'complex-200-{index:04d}'
        messages, entities, services, ledger, first_alert = [], [], [], [], None
        for n, (template, is_trigger) in enumerate(steps, 1):
            message, spans, flags = materialize(template, profiles, f'm{n}', len(entities))
            messages.append(message); entities.extend(spans); services.extend(flags)
            if is_trigger:
                first_alert = n
            ledger.append(dict(message_id=f'm{n}', expected_first_alert=first_alert,
                               reason=rationale if first_alert else 'Недостаточно личных атрибутов одного владельца',
                               source='authored_qualitative_TZ_rule_not_runtime_weights'))
        doc = dict(schema_version='annotation-mvp-v2', session_id=sid, messages=messages,
                   entities=entities, case_flags=FLAGS.copy())
        validate(doc)
        owners = {p for e in entities for p in (e['person_id'] if isinstance(e['person_id'], list)
                                               else [e['person_id']]) if p is not None}
        assert len(owners) == people and 5 <= len(messages) <= 10
        record = dict(session_id=sid, family=family, variant=variant, people=people, gold=doc,
                      expected_first_alert=first_alert, author_alert_rationale=rationale,
                      gold_service_contacts=services, author_prefix_ledger=ledger)
        records.append(record); sessions.append(dict(session_id=sid, messages=messages))
        plan.append(dict(session_id=sid, family=family, people=people, variant=variant,
                         authored_first_alert=first_alert, rationale=rationale))
        length_counts[len(messages)] += 1; owners_counts[people] += 1
        review.extend([f'## {sid} — {family}', '', *[f"**{m['id']}** {m['text']}\n" for m in messages],
                       '| Сообщение | Фрагмент | Тип | Владельцы |', '|---|---|---|---|'])
        texts = {m['id']: m['text'] for m in messages}
        review.extend(f"| {e['message_id']} | {texts[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |"
                      for e in entities)
        review.extend([f'Авторский первый алерт: {first_alert}; основание: {rationale}.', ''])
    manifest = dict(version=VERSION, seed=seed, synthetic_only=True, message_selection='user_only',
        sessions=len(records), messages=sum(len(r['gold']['messages']) for r in records),
        entities=sum(len(r['gold']['entities']) for r in records),
        positive_sessions=sum(r['expected_first_alert'] is not None for r in records),
        lengths=dict(sorted(length_counts.items())), people=dict(sorted(owners_counts.items())),
        families=dict(Counter(r['family'] for r in records)),
        annotation_source='assistant_authored_scenario_and_explicit_span_owner_ledger',
        human_review_status='pending', external_generation_api=False, service_weights_used_for_gold=False,
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        limitations=['20 authored plots with 10 profile/context variants each, not 200 independent plots',
                     'synthetic names and nonworking telephone code 000', 'only user messages, no assistant turns',
                     'not representative of real traffic', 'late ownership does not rewrite previous null mentions'])
    output.mkdir(parents=True)
    for filename, value in [('snapshot.json', dict(**manifest, records=records)),
                             ('sessions.json', dict(synthetic_only=True, sessions=sessions)),
                             ('gold.json', dict(annotations=[r['gold'] for r in records])),
                             ('scenario-plan.json', dict(scenarios=plan)), ('manifest.json', manifest)]:
        write(output / filename, value)
    (output / 'sessions-for-review.md').write_text('\n'.join(review), encoding='utf-8')
    (output / 'generation-prompt.md').write_text(
        '# Авторское задание\n\n200 синтетических диалогов по 5–10 сообщений пользователя, '
        '2–3 человека, реалистичная задача поддержки. Добавить общие и раздельные контакты, '
        'одноимённых людей, смену пишущего, позднее уточнение, повторы, служебные контакты, '
        'адрес доставки отдельно от проживания. Разметить все упоминания вручную в шаблонах; '
        'границы материализовать кодом. Владельцев и первый алерт задать независимо от сервиса. '
        'Внешняя генерация и автоматическая NER-разметка эталона не используются.\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/experiments/complex-200-2026-11-25')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'data'):
        parser.error('Данные должны оставаться в data/')
    print(json.dumps(build(args.output.resolve()), ensure_ascii=False))
