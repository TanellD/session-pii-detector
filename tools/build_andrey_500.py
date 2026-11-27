"""500 авторских сессий строго по исходным 16 сценариям Андрея."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import random
import re

from annotation_calculator import Calculator
from annotation_contract import assess
from run_compatible_annotation_pilot import materialize_quotes
from session_alert_review import validate_review_document

ROOT = Path(__file__).resolve().parents[1]
FLAGS = dict(is_example=False,annotation_uncertain=False,clarification_reason=None)

# Каждая строка — самостоятельный авторский вариант реплики исходного сценария.
# Слоты сущностей, владельцы и порядок фактов задаются отдельно от формулировок.
BANKS = {
 'dev-01': [
  ['Потерялся читательский билет. Можно заказать новый в чате?', 'Не нахожу свой билет в библиотеку. Как его восстановить?',
   'Хочу перевыпустить библиотечный билет, не приезжая лично.', 'Билет остался в старой сумке, а она потерялась. Как получить новый?',
   'Через личный кабинет можно подать заявку на замену читательского билета?', 'Нужен новый читательский билет. Здесь принимают такие заявки?'],
  ['Для заявки: меня зовут {name}, моя фамилия {surname}.', 'В библиотечной анкете моё имя {name}, фамилия {surname}.',
   'Запишите мои данные: имя {name}, фамилия {surname}.', 'Сначала представлюсь: {name} {surname}. Заявку подаю на себя.',
   'Оформляю на себя. Моё имя {name}, фамилия {surname}.', 'В анкете библиотеки я указана как {name} {surname}.'],
  ['Уведомление мне можно отправить на личную почту {email}.', 'Моя почта для ответа: {email}.',
   'Личный email у меня {email}, туда удобно получить подтверждение.', 'Для связи оставлю свою почту — {email}.',
   'Мой электронный адрес {email}. Подтверждение заявки жду туда.', 'Напишите, пожалуйста, на мою личную почту {email}.']],
 'dev-02': [
  ['На экскурсию записывал я. Мой телефон {phone}.', 'У меня бронь на экскурсию; связаться со мной можно по {phone}. Это мой телефон.',
   'Хочу проверить свою запись в музей. Мой контактный телефон {phone}.', 'Заказывал экскурсию на двоих, мой личный номер {phone}.',
   'Моя запись на экскурсию ещё действует? Для связи мой телефон {phone}.', 'Пишу по своей брони в музей. Мой номер {phone}.'],
  ['Спутнику нужен отдельный билет. Его личная почта {email}.', 'Со мной будет спутник. Его email для билета: {email}.',
   'Второй билет отправьте моему спутнику на его почту {email}.', 'У спутника отдельная почта {email}; билет для него отправьте туда.',
   'Мой спутник просит прислать свой билет на {email}. Это его личная почта.', 'Отдельно оставлю личную почту спутника — {email}.'],
  ['Мой телефон для изменений остаётся {phone}.', 'Если перенесётся время, звоните мне на мой номер {phone}.',
   'Повторю свой телефон: {phone}, он не менялся.', 'Для срочной связи со мной тот же личный номер {phone}.',
   'Подтверждаю мой телефон {phone}.', 'По своей записи жду звонка на мой телефон {phone}.']],
 'dev-03': [
  ['Расходы для возмещения нужно вносить отдельно или общей суммой?', 'Заполняю заявление на возмещение. Как приложить расходы?',
   'На форме компенсации есть несколько строк. В какую записать расходы?', 'Можно приложить одну квитанцию к заявлению на возмещение?',
   'Не понимаю, как заполнить раздел расходов для компенсации.', 'Помогите с формой возмещения: расходы разбивать по строкам?'],
  ['В приложенной записи указан ИНН: {inn}. Из подписи неясно, чей он.', 'Вижу в приложении ИНН {inn}, владелец не подписан.',
   'Скопировал из записи ИНН: {inn}. Кому он принадлежит, не знаю.', 'В документе стоит ИНН {inn}. Человек рядом не указан.',
   'Там есть строка «ИНН: {inn}», но имя владельца отсутствует.', 'В старой записи сохранился ИНН {inn}; чей именно, пока неизвестно.']],
 'dev-04': [
  ['Хочу перенаправить посылку. Я {surname} {name} {patronymic}.', 'По доставке нужна смена адреса. Меня зовут {name} {patronymic} {surname}.',
   'Заявка моя: {surname} {name} {patronymic}. Можно изменить доставку?', 'Посылка оформлена на меня, {name} {patronymic} {surname}.',
   'Прошу изменить доставку моей посылки. Я {surname} {name} {patronymic}.', 'Представлюсь для заявки: {name} {patronymic} {surname}. Пишу о своей посылке.'],
  ['Мой адрес проживания: {address}.', 'Я живу по адресу {address}. Прошу направить посылку сюда.',
   'Для доставки укажу адрес, где живу: {address}.', 'Проживаю по адресу {address}, туда нужна посылка.',
   'Отправьте туда, где я живу: {address}.', 'Место моего проживания — {address}.'],
  ['В адресе проживания ошибся с квартирой, исправляю: {address_new}.', 'Уточню мой адрес проживания: {address_new}. Раньше квартиру указал неверно.',
   'Правильный адрес, где я живу: {address_new}. Предыдущую запись прошу заменить.', 'Поправка к моему адресу: {address_new}. Это мой адрес проживания.',
   'Нужна правка квартиры в моём адресе проживания: {address_new}.', 'Проверил запись: мой адрес проживания всё-таки {address_new}.']],
 'dev-05': [
  ['Где отключаются рекомендации в ленте?', 'Хочу убрать рекомендованные публикации. Как это сделать?', 'Можно скрыть рекомендации в приложении?',
   'Не нахожу настройку рекомендаций. Подскажите путь.', 'В ленте слишком много рекомендаций, хочу их отключить.', 'Как оставить ленту без рекомендованных записей?'],
  ['При этом сообщения об обновлениях хочу оставить.', 'Уведомления об обновлениях мне нужны, их не выключать.',
   'Мне важно сохранить только уведомления о новых версиях.', 'Остальные рекомендации не нужны, а сообщения об обновлениях нужны.',
   'Уточню: отключаю рекомендации, но не уведомления об обновлениях.', 'После этого уведомления об обновлениях будут приходить? Их хочу сохранить.'],
  ['Нашёл нужный переключатель в настройках, спасибо.', 'Получилось отключить через настройки.', 'Уже разобрался с переключателем, благодарю.',
   'Проверил ленту, лишние рекомендации исчезли.', 'Настройка сохранилась, теперь всё как нужно.', 'Перезапустил приложение, настройка сработала.']],
 'dev-06': [
  ['Мне {age}. Можно записаться в вечернюю группу со скидкой?', 'Хочу в вечернюю группу; мне {age}. Как подать заявку на скидку?',
   'Есть ли скидка на вечерний поток? Мой возраст — {age}.', 'Мне {age}, ищу вечерний вариант обучения со скидкой.',
   'Для заявки укажу возраст: {age}. Интересует вечерняя группа.', 'Вечером удобнее заниматься. Мне {age}; как оформить скидку?'],
  ['Мой телефон для звонка {phone}.', 'По заявке можно позвонить мне на личный номер {phone}.', 'Для связи со мной мой телефон {phone}.',
   'Оставлю свой номер для подтверждения: {phone}.', 'Мой контактный телефон — {phone}.', 'Номер моего телефона {phone}, по нему удобно обсудить запись.'],
  ['Моя личная почта для подтверждения {email}.', 'Подтверждение можно прислать на мой email {email}.', 'И моя почта — {email}.',
   'Своя почта для заявки у меня {email}.', 'Дополнительно оставлю личную почту {email}.', 'Для письма используйте мой личный адрес {email}.']],
 'dev-07': [
  ['Я работаю корректором. В кабинете неверно указан стаж, как поправить?', 'Работаю корректором, хочу исправить стаж в анкете.',
   'Моя профессия — корректор. В профиле ошибка в стаже.', 'В трудовой анкете надо исправить стаж; я корректор.',
   'В личном кабинете не сохраняется мой стаж. Работаю корректором.', 'Я корректор, а стаж в профиле подтянулся неправильно.'],
  ['Мой работодатель — {organization}.', 'Работаю в {organization}.', 'В графе моего работодателя должно быть {organization}.',
   'Организация, где я работаю: {organization}.', 'Уточню работодателя: {organization}.', 'У меня работодатель {organization}, это тоже нужно сохранить.'],
  ['Ещё в профиле ошибка в поле пола: я женщина.', 'В разделе пола нужно исправление: я женщина.', 'Прошу указать мой пол — женский.',
   'В анкете у меня неверный пол. Правильно: женский.', 'И ещё поправка к моей анкете: мой пол женский.', 'Отдельно уточню про себя: я женщина.']],
 'dev-08': [
  ['Бронировал инструменты на выходные. Можно оплатить при получении?', 'По аренде комплекта инструментов: оплата возможна на месте?',
   'Комплект инструментов забронирован. Нужно платить сейчас или при выдаче?', 'Хочу уточнить оплату моей брони инструментов на выходные.',
   'На выходные оформлена аренда инструментов. Подойдёт оплата при выдаче?', 'Пишу по брони инструментов: можно рассчитаться в пункте выдачи?'],
  ['Мой паспорт: {passport}. Нужно отдельно подтверждать получение?', 'Для моей заявки паспорт {passport}. Как подтвердить выдачу?',
   'Укажу свой паспорт: {passport}. Его достаточно для получения?', 'Мой паспорт {passport}; отдельно заявку на выдачу подавать?',
   'В моей анкете паспорт {passport}. Ещё что-нибудь нужно для получения?', 'Для проверки брони оставлю свой паспорт: {passport}.']],
 'test-01': [
  ['Меня зовут {name}. Хочу перейти в утреннюю группу.', 'Я {name}, пишу по переводу в утренний поток.', 'Можно сменить вечерний поток на утренний? Моё имя {name}.',
   'Подаю заявку на утренний поток для себя. Меня зовут {name}.', 'Хочу перенести обучение на утро. Я {name}.', 'По своей записи хочу сменить поток. Моё имя — {name}.'],
  ['Моё отчество {patronymic}, если так проще найти заявку.', 'Дополню свои данные: отчество {patronymic}.', 'В заявке моё отчество — {patronymic}.',
   'Для поиска записи уточню моё отчество: {patronymic}.', 'Забыл указать своё отчество: {patronymic}.', 'По моим данным ещё отчество {patronymic}.'],
  ['Мой телефон для подтверждения переноса {phone}.', 'По переносу можно позвонить мне на мой номер {phone}.', 'Для связи оставлю свой телефон {phone}.',
   'Мой личный номер {phone}; по нему удобно подтвердить перевод.', 'Телефон у меня {phone}, жду подтверждения переноса.', 'Подтверждение перевода жду по своему телефону {phone}.']],
 'test-02': [
  ['Я {name} {surname}, оформляла возврат в приложении.', 'Пишу о своём возврате. Меня зовут {name}, фамилия {surname}.',
   'Заявка на возврат моя, {name} {surname}.', 'У меня возврат товара. В заявке я {name} {surname}.',
   'По возврату представлюсь: {name} {surname}.', 'Возврат оформлен на меня. Моё имя {name}, фамилия {surname}.'],
  ['Сейчас товар у получателя. Он живёт по адресу {address}.', 'Забирать нужно у другого получателя. Его адрес проживания: {address}.',
   'Получатель другой человек; место его проживания — {address}. Там находится товар.', 'Товар пока у получателя. Адрес, где он живёт: {address}.',
   'Адрес проживания получателя {address}; это не мой адрес.', 'Для курьера укажу адрес проживания другого получателя: {address}.'],
  ['Курьера можем дождаться до пятницы.', 'С передачей товара можем подождать несколько дней.', 'Пока товар оставим у получателя до приезда курьера.',
   'Нужна только информация о дне приезда курьера.', 'Упаковка целая, дождёмся курьера.', 'Можем согласовать вывоз на ближайший будний день.']],
 'test-03': [
  ['Меня зовут {name}, второго организатора — {other}.', 'Я {name}. Вместе со мной встречу организует {other}.',
   'Организуем вдвоём: меня зовут {name}, второго организатора зовут {other}.', 'По расписанию клуба пишу я, {name}. Ещё один организатор — {other}.',
   'Нас два организатора, я {name}, другой — {other}.', 'Встречу ведём мы вдвоём. Моё имя {name}, имя второго организатора {other}.'],
  ['Наш общий личный телефон {phone}, он принадлежит нам обоим.', 'Для связи с нами обоими наш личный общий номер {phone}.',
   'У нас двоих один личный телефон: {phone}. Это не телефон клуба.', 'Оба пользуемся личным общим номером {phone}.',
   'Личный телефон у нас совместный — {phone}, владельцы мы оба.', 'Наш с вторым организатором общий личный номер {phone}.'],
  ['А моя личная почта {email}, туда пришлите расписание.', 'Расписание удобно получить мне на мою почту {email}.',
   'Отдельно оставлю только свой личный email {email}.', 'Мой собственный электронный адрес {email}, не общий.',
   'Письмо с расписанием жду на личную почту {email}, она только моя.', 'У меня есть своя почта {email}, пришлите расписание туда.']],
 'test-04': [
  ['В списке участников почта {email}. Пока не знаю, чья она.', 'Сохранился email {email}, владелец ещё неизвестен.',
   'В старой записи вижу почту {email}. Не понимаю, кому она принадлежит.', 'Нашёл в списке {email}, пока без сведений о владельце.',
   'Есть адрес почты {email}. Не могу сейчас сказать, чей он.', 'В записи участника стоит email {email}; человек не подписан.'],
  ['Разобрался: та почта моя. Меня зовут {name}.', 'Выяснил у организаторов, что тот адрес мой. Моё имя {name}.',
   'Теперь уточню: речь была о моей почте. Я {name}.', 'Да, нашёл свою запись, та почта принадлежит мне. Меня зовут {name}.',
   'Оказалось, это моя почта. Для записи моё имя {name}.', 'Уточнение по прежней записи: почта моя, имя у меня {name}.'],
  ['Мой телефон на случай переноса встречи {phone}.', 'Если время встречи изменится, мой личный номер {phone}.',
   'Для срочной связи оставлю свой телефон {phone}.', 'Мой номер телефона {phone}, на случай изменений.',
   'Позвонить мне можно по моему номеру {phone}.', 'По встрече используйте мой личный телефон {phone}.']],
 'test-05': [
  ['Можно выгрузить историю операций в таблицу?', 'Хочу скачать историю операций. Где экспорт?', 'Как сохранить список операций отдельным файлом?',
   'Нужна таблица с историей операций, в приложении не нахожу выгрузку.', 'Есть ли экспорт операций из кабинета?', 'Как выгрузить операции, чтобы открыть их в таблице?'],
  ['Подойдёт формат, который открывается без установки приложения.', 'Важно, чтобы файл можно было открыть обычным просмотрщиком.',
   'Отдельную программу ставить не хочу, нужен обычный табличный формат.', 'Формат не принципиален, лишь бы не требовалось новое приложение.',
   'Можно любой табличный файл без специального приложения.', 'Достаточно формата для обычной таблицы.'],
  ['Выгрузка получилась, спасибо.', 'Файл скачался и открылся, благодарю.', 'Нашёл экспорт в меню, всё получилось.',
   'Таблица уже открылась, вопрос решён.', 'Сохранил файл, теперь вижу операции.', 'Повторил выгрузку, на этот раз файл открылся.']],
 'test-06': [
  ['Заявление не двигается со вчерашнего дня. Я живу в {geography}.', 'По заявлению нет обновлений. Я живу в {geography}.',
   'Я живу в {geography}, заявление на выплату зависло.', 'Нужна проверка зависшего заявления. Проживаю в {geography}.',
   'В кабинете статус не меняется. Живу в {geography}.', 'Заявление на выплату осталось без движения; живу в {geography}.'],
  ['Мой СНИЛС: {snils}. Прошу проверить карточку заявления.', 'Для моей карточки СНИЛС {snils}. Где посмотреть статус?',
   'В анкете мой СНИЛС {snils}. Заявление по нему находится?', 'Мой СНИЛС — {snils}, хочу проверить заявку.',
   'Укажу свой СНИЛС: {snils}. Помогите найти заявление.', 'В моих данных СНИЛС {snils}. Статус пока прежний.'],
  ['Моя карта: {card}. Можно изменить способ выплаты?', 'Для выплаты у меня карта {card}. Как выбрать другой способ?',
   'В форме моя карта {card}. Можно получать выплату иначе?', 'Моя карта указана так: {card}. Где изменить способ получения?',
   'Ещё в анкете моя карта {card}; хочу поменять способ выплаты.', 'По выплате уточню свою карту: {card}. Можно обойтись без неё?']],
 'test-07': [
  ['Мне {age}. Как проверить камеру перед экзаменом?', 'Хочу заранее протестировать камеру; мне {age}.', 'Мой возраст {age}. Есть пробная проверка камеры?',
   'Подаю заявку на онлайн-экзамен. Мне {age}; как протестировать подключение?', 'Мне {age}, готовлюсь к экзамену. Где тест камеры?', 'Перед экзаменом нужна проверка камеры. Мой возраст — {age}.'],
  ['В анкете проверьте ещё пол: я мужчина.', 'Заодно уточню поле пола: я мужчина.', 'В моей анкете пол должен быть мужской.',
   'Уточнение к моим данным: мой пол мужской.', 'Проверьте запись о поле, я мужчина.', 'В разделе пола у меня должно стоять «мужской».'],
  ['Я работаю реставратором, подключиться смогу после обеда.', 'Моя профессия — реставратор, поэтому удобно пройти проверку после работы.',
   'Работаю реставратором и смогу проверить камеру ближе к вечеру.', 'По времени мне удобнее после обеда: я реставратор.',
   'Я реставратор, на работе проверить камеру не получится.', 'Работаю реставратором, подключусь к проверке позже.']],
 'test-08': [
  ['Не могу войти в кабинет. Мой телефон {phone}, моя почта {email}.', 'Восстанавливаю доступ к своей учётной записи. Мои контакты: телефон {phone}, почта {email}.',
   'Вход перестал работать. Для моего аккаунта телефон {phone} и личный email {email}.', 'Пишу по своему кабинету: мой номер {phone}, мой электронный адрес {email}.',
   'Хочу восстановить свой доступ. Мой телефон {phone}, личная почта {email}.', 'Не получается войти. Ко мне относятся телефон {phone} и почта {email}.'],
  ['Повторю свою почту для восстановления: {email}.', 'Мой email тот же, {email}.', 'Для письма о доступе ещё раз моя личная почта {email}.',
   'Почта у меня {email}, повторяю на случай ошибки.', 'Уточняю написание моей почты: {email}.', 'Проверьте, пожалуйста, мою почту {email}.'],
  ['Письмо пришло, спасибо.', 'Письмо для восстановления уже вижу.', 'Получил письмо, дальше попробую восстановить доступ.',
   'Теперь письмо нашлось во входящих.', 'Проверил почту, сообщение о восстановлении дошло.', 'Уже получил письмо с восстановлением, благодарю.']],
}

ALERTS = {'dev-01':(3,'combination_rule'), 'dev-04':(2,'combination_rule'),
          'dev-06':(3,'combination_rule'),'dev-08':(2,'red_rule'),
          'test-01':(3,'combination_rule'),'test-03':(3,'combination_rule'),'test-08':(1,'combination_rule')}


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source,original,output,seed=2026112302):
    source,original,output = Path(source).resolve(),Path(original).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        raise ValueError('Нужен новый каталог внутри data/')
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    original_ids = {s['id'] for s in read(original/'scenario-plan.json')['scenarios']}
    if original_ids != set(BANKS):
        raise ValueError('Исходные сценарии Андрея не совпадают с авторским набором вариантов')
    plan = read(source/'scenario-plan.json')
    scenarios = {s['id']:s for s in plan['scenarios'] if s['id'] in original_ids}
    authored = {s['id']:s for s in read(source/'authored-dialogues.json') if s['id'] in original_ids}
    if set(scenarios)!=original_ids or set(authored)!=original_ids:
        raise ValueError('Миграция потеряла исходный сценарий')
    rng = random.Random(seed)
    calculator = Calculator()
    rows = []
    for scenario_index,sid in enumerate(sorted(original_ids)):
        combos = list(itertools.product(range(6),repeat=len(BANKS[sid])))
        rng.shuffle(combos)
        for variant in range(32 if scenario_index < 4 else 31):
            rows.append((sid,variant,combos[variant]))
    rng.shuffle(rows)
    sessions, gold, checks, prefixes, new_plan, ledger = [],[],[],[],[],[]
    families, splits = Counter(),Counter()
    notes = ['# Авторские диалоги по исходным сценариям Андрея', '',
             '500 сессий; новые формулировки, исходные 16 сценариев. Авторская разметка до внешней модели.', '']
    for index,(sid,variant,combo) in enumerate(rows,1):
        scenario,old = scenarios[sid],authored[sid]
        female = sid in {'dev-01','test-02'}
        names = ['Макетослава','Схемослава','Черновислава','Пробослава','Эскизослава','Шаблонислава'] if female else ['Черновимир','Схемослав','Пробомир','Шаблоний','Эскизимир','Макетослав']
        surnames = ['Схемовикова','Макетникова','Эскизонова','Пробникова','Шаблоникова','Черновиковская'] if female else ['Схемовиков','Макетников','Эскизонов','Пробников','Шаблоников','Черновиковский']
        values = dict(name=names[variant%6],surname=surnames[(variant//2)%6],patronymic=['Шаблонович','Эскизович','Схемович','Макетович','Пробович','Черновикович'][variant%6],
                      other=['Ладомакета','Схемослава','Пробослава','Макетослава','Эскизослава','Черновислава'][(variant+2)%6],
                      phone=f'+7 (000) {index//10000:03d}-{index//100%100:02d}-{index%100:02d}',
                      email=f'{scenario["family"].replace("-","")}.{index}@example.invalid',
                      address=f'г. Схемоград, ул. Эскизная, д. {10+variant}, кв. {20+variant}',
                      address_new=f'г. Схемоград, ул. Эскизная, д. {10+variant}, кв. {70+variant}',
                      organization=f'ООО «Черновой оттиск {variant+1}»',passport=f'00 00 {index:06d}',
                      # Фиксированные отрицательные документы — часть исходных сценариев.
                      inn='000000000031',snils='000-000-000 16',card='0000 0000 0000 0162',
                      geography='Тестосельске',age=f'{28+variant%12} лет' if sid=='dev-06' else f'{46+variant%12} лет')
        # Корректное склонение единицы возраста без вычисления новых фактов.
        age_number=int(values['age'].split()[0])
        unit='лет' if 11 <= age_number%100 <=14 or age_number%10 not in {1,2,3,4} else 'год' if age_number%10==1 else 'года'
        values['age']=f'{age_number} {unit}'
        texts,entities,steps = [],[],[]
        used_address=False
        for m,(bank,choice,old_row) in enumerate(zip(BANKS[sid],combo,old['messages'],strict=True),1):
            template=bank[choice]
            text=template.format(**values)
            # Варианты профессии/пола размечаются по фактически написанной форме.
            spans=[]
            for kind,old_value,owner in old_row[1:]:
                slot = {'first_name':'other' if isinstance(owner,str) and owner=='cohost' else 'name',
                        'last_name':'surname','patronymic':'patronymic','phone':'phone','email':'email',
                        'address':'address_new' if used_address else 'address','passport':'passport',
                        'geography':'geography','age':'age','organization':'organization'}.get(kind)
                if slot:
                    quote=values[slot]
                elif kind=='occupation':
                    candidates=['корректором','корректор'] if sid=='dev-07' else ['реставратором','реставратор']
                    quote=next(q for q in candidates if q in text)
                elif kind=='sex':
                    quote=next(q for q in ['женщина','женский','мужчина','мужской'] if q in text)
                else:
                    raise ValueError(f'Незапланированный класс: {kind}')
                if text.count(quote)!=1:
                    raise ValueError(f'{sid}/{m}: неуникальная цитата {quote}')
                start=text.index(quote)
                # Именные формы сохраняют локальные ID исходного сценария.
                spans.append(dict(id=f'e{len(entities)+len(spans)+1}',message_id=f'm{m}',type=kind,
                                  person_id=owner,start=start,end=start+len(quote)))
            if any(e['type']=='address' for e in spans):
                used_address=True
            texts.append(dict(id=f'm{m}',text=text))
            entities.extend(spans)
            steps.append([f"{'shared' if isinstance(e['person_id'],list) else e['person_id'] or 'unknown'}:{e['type']}" for e in spans])
        # Дополнительные нейтральные реплики не требуют скрытых ответов помощника.
        before = variant%4==0
        after = variant%4 in {1,2}
        if before:
            texts.insert(0,dict(id='temporary',text=['Здравствуйте, хочу уточнить один вопрос.','Пишу с телефона, могу отправить сведения несколькими сообщениями.','Добрый день. Не получается закончить оформление.'][variant//4%3]))
            for e in entities:
                e['message_id']=f'm{int(e["message_id"][1:])+1}'
            steps.insert(0,[])
        if after:
            texts.append(dict(id='temporary',text=['Пока жду подтверждения в кабинете.','Если понадобятся ещё сведения, напишу отдельно.','Проверю статус ещё раз немного позже.','Это всё, что могу сейчас уточнить.'][variant//4%4]))
            steps.append([])
        for m,text in enumerate(texts,1):
            text['id']=f'm{m}'
        session_id=f'andrey-500-{index:04d}'
        session=dict(session_id=session_id,messages=texts)
        doc=dict(**session,schema_version='annotation-mvp-v2',entities=entities,case_flags=FLAGS.copy())
        validate_review_document(doc)
        assessment=assess(doc,calculator)
        expected=ALERTS.get(sid)
        expected=(f'm{expected[0]+int(before)}',expected[1]) if expected else None
        actual=assessment['first_alert']
        signature=(actual['message_id'],actual['reason']) if actual else None
        if signature!=expected:
            raise ValueError(f'{sid}/{variant}: author {expected}, calculator {signature}')
        doc['case_flags']=assessment['case_flags']
        for length in range(1,len(texts)+1):
            prefix={**doc,'messages':texts[:length],'entities':[e for e in entities if int(e['message_id'][1:])<=length], 'case_flags':FLAGS.copy()}
            result=assess(prefix,calculator)
            prefixes.append(dict(session_id=session_id,prefix_length=length,assessment=result))
            first_expected=expected if expected and int(expected[0][1:])<=length else None
            ledger.append(dict(session_id=session_id,message_id=f'm{length}',expected_first_alert=first_expected,
                basis='author_TZ_rule_before_external_model; no_runtime_weights',
                rule=first_expected[1] if first_expected else 'no_sufficient_rule',
                note='unknown_owner_not_a_person; previous_null_frozen; shared_fact_per_owner'))
        sessions.append(session)
        gold.append(doc)
        checks.append(dict(session_id=session_id,family=scenario['family'],source_scenario_id=sid,
                           variant=variant,split=scenario['split'],author_expected_first_alert=expected,assessment=assessment))
        new_plan.append(dict(id=session_id,source_scenario_id=sid,family=scenario['family'],split=scenario['split'],
                             profiles=[f'{sid}-profile-{variant}'],steps=steps))
        families[scenario['family']]+=1
        splits[scenario['split']]+=1
        notes += [f'## {session_id} — {sid}, {scenario["family"]}', '']
        notes += [f"**{m['id']}:** {m['text']}\n" for m in texts]
        notes += ['| Сообщение | Фрагмент | Тип | Владельцы |','|---|---|---|---|']
        by_id={m['id']:m['text'] for m in texts}
        notes += [f"| {e['message_id']} | {by_id[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |" for e in entities]
        notes += [f'Авторский первый алерт: {expected}.','']
    manifest=dict(version='andrey-original-scenarios-500-2026-11-23',message_selection='user_only',synthetic_only=True,
        sessions=len(sessions),messages=sum(len(s['messages']) for s in sessions),entities=sum(len(d['entities']) for d in gold),
        positive_sessions=sum(bool(c['assessment']['first_alert']) for c in checks),families=dict(sorted(families.items())),splits=dict(splits),
        source_scenario_ids=sorted(original_ids),new_integration_scenarios_included=False,
        original_plan_sha256=digest(original/'scenario-plan.json'),original_dialogues_sha256=digest(original/'authored-dialogues.json'),
        migrated_plan_sha256=digest(source/'scenario-plan.json'),migrated_dialogues_sha256=digest(source/'authored-dialogues.json'),
        generator_sha256=digest(Path(__file__)),seed=seed,policy_version=calculator.policy['version'],
        annotation_source='interactive_assistant_authored_new_utterance_banks_and_independent_owner_ledger',
        human_review_status='pending',external_generation_api=False,service_weights_used_for_gold=False,
        limitations=['controlled authored wording banks, not 500 independent plots','user-only, no assistant turns','no real user distribution',
                     'scenario grouping inherited; domains confounded with development/test','document values and phone code deliberately synthetic'])
    output.mkdir(parents=True)
    for filename,value in [('sessions.json',dict(synthetic_only=True,sessions=sessions)),('gold.json',dict(annotations=gold)),
        ('gold-assessments.json',dict(checks=checks)),('gold-prefixes.json',dict(steps=prefixes)),
        ('scenario-plan.json',dict(version=manifest['version'],scenarios=new_plan,review_policy='pr46-review-v2')),
        ('author-alert-ledger.json',dict(steps=ledger)),('manifest.json',manifest)]:
        write(output/filename,value)
    (output/'sessions-for-review.md').write_text('\n'.join(notes),encoding='utf-8')
    (output/'generation-prompt.md').write_text(
        '# Авторское задание\n\nИспользовать только исходные 16 сценариев Андрея (dev-01..08, test-01..08). '
        'Не добавлять 8 сценариев интеграции. Сохранить задачу пользователя, владельцев и порядок раскрытия атрибутов. '
        'Составить новые разговорные реплики до внешней проверки. Варьировать обращения, просьбы, формулировки, '
        'порядок частей имени внутри реплики и нейтральные сообщения. '
        '500 вариантов распределить по исходным сценариям; разбиение наследовать по семье, не перемешивать профили. '
        'Использовать текущие правила checksum и совместной принадлежности. '
        'Сначала авторские точные спаны, владельцы и ожидаемый первый алерт по ТЗ, затем независимый вызов Gemma без gold. '
        'Имена вымышленные; почта example.invalid; телефон с нерабочим кодом 000. '
        'Реализация — явные авторские банки реплик tools/build_andrey_500.py; свободная генерация внешней моделью не выполнялась.\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='Мигрированный корпус с текущей политикой')
    parser.add_argument('--original',type=Path,required=True,help='Переданный исходный корпус Андрея v1')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.source,args.original,args.output),ensure_ascii=False))
