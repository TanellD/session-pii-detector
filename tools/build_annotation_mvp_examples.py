"""Воспроизводимые вымышленные примеры и разбор разногласий для инструкции MVP."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHONE = "+7 (000) 000-00-01"  # Намеренно несуществующий код, не реальный контакт.
EMAIL = "testimir@example.invalid"
FIO = "Тестов Тестимир Макетович"
ADDRESS = "г. Макетоград, ул. Вымышленная, д. 1"


def build():
    cases = []

    def add(key, title, messages, labels, first, disagreement, resolution):
        entities = []
        for number, needle, kind, person in labels:
            text = messages[number - 1]
            start = text.index(needle)
            # ФИО — только удобное обозначение в исходнике вымышленного кейса.
            # В разметку попадают отдельные фамилия, имя и отчество.
            components = list(zip(needle.split(), ("last_name", "first_name", "patronymic"))) if kind == "fio" else [(needle, kind)]
            offset = start
            for value, part_kind in components:
                part_start = text.index(value, offset)
                entities.append({"id": f"e{len(entities) + 1}", "message_id": f"m{number}",
                                 "start": part_start, "end": part_start + len(value), "type": part_kind, "person_id": person})
                offset = part_start + len(value)
        cases.append({"id": key, "title": title, "annotation": {
            "schema_version": "annotation-mvp-v2", "session_id": "synthetic-" + key,
            "case_flags": {"is_example": key in {"quoted_unknown", "explicit_example_with_alert"},
                           "annotation_uncertain": False, "clarification_reason": None},
            "messages": [{"id": f"m{i}", "text": text} for i, text in enumerate(messages, 1)], "entities": entities},
            "expected_first_alert": {"message_id": f"m{first[0]}", "reason": first[1]} if first else None,
            "disagreement": disagreement, "resolution": resolution})

    add("full_name_alone", "ФИО без дополнительного признака", [f"Меня зовут {FIO}."],
        [(1, FIO, "fio", "p1")], None, "А: ФИО достаточно. Б: нужен дополнительный признак.",
        "Прав Б. ФИО — один оранжевый маркер; самостоятельного правила ФИО нет.")
    add("io_phone", "ИО и телефон в разных сообщениях", ["Меня зовут Тестимир.", "Моё отчество Макетович.", f"Мой телефон {PHONE}."],
        [(1, "Тестимир", "first_name", "p1"), (2, "Макетович", "patronymic", "p1"), (3, PHONE, "phone", "p1")],
        (3, "combination_rule"), "А: алерт на ИО. Б: после телефона.", "Прав Б: сначала собрана пара, затем отдельный оранжевый признак.")
    add("phone_before_fo", "Телефон появился до ФО", [f"Мой телефон {PHONE}.", "Моя фамилия Тестов.", "Отчество Макетович."],
        [(1, PHONE, "phone", "p1"), (2, "Тестов", "last_name", "p1"), (3, "Макетович", "patronymic", "p1")],
        (3, "combination_rule"), "А: порядок признаков имеет значение. Б: условие собрано в третьем сообщении.", "Прав Б: используется первый полный префикс, порядок основания не важен.")
    add("fi_address_without_population", "ФИ и полный адрес без числа жильцов", ["Я Тестов Тестимир.", f"Я живу по адресу: {ADDRESS}."],
        [(1, "Тестов", "last_name", "p1"), (1, "Тестимир", "first_name", "p1"), (2, ADDRESS, "address", "p1")],
        (2, "combination_rule"), "А: нет числа жильцов — нельзя алертить. Б: достаточно отдельного полного адреса.",
        "Прав Б по новому качественному правилу; число жильцов для этого основания не требуется.")
    add("different_people", "Части относятся к разным людям", ["Я Тестимир. Мой коллега — Тестов.", f"Мой телефон {PHONE}."],
        [(1, "Тестимир", "first_name", "p1"), (1, "Тестов", "last_name", "p2"), (2, PHONE, "phone", "p1")],
        None, "А: есть ФИ и телефон в сессии. Б: ФИ одного человека нет.", "Прав Б: факты разных персон не объединяются.")
    add("quoted_unknown", "Цитата с ФИО и email", [f"В учебном примере написано: «{FIO}, {EMAIL}»."],
        [(1, FIO, "fio", "p_quote1"), (1, EMAIL, "email", None)], None,
        "А: связать всё с говорящим. Б: отдельная персона цитаты, принадлежность почты не доказана.",
        "Прав Б. Кейс отмечен как пример, для явно названного человека создана отдельная персона. Соседство email с ФИО не создаёт личную принадлежность автоматически.")
    add("shared_contact_unknown", "Контакт службы рядом с ФИО", [f"Я {FIO}. Телефон приёмной: {PHONE}."],
        [(1, FIO, "fio", "p1"), (1, PHONE, "phone", None)], None,
        "А: телефон расположен рядом с ФИО. Б: контакт приёмной не принадлежит человеку.", "Прав Б: телефон выделяется, person_id=null.")
    add("initials_not_annotated", "Инициалы вне MVP", ["Я Тестов Т. М.", f"Мой телефон {PHONE}."],
        [(1, "Тестов", "last_name", "p1"), (2, PHONE, "phone", "p1")], None,
        "А: разметить Т. как имя и М. как отчество. Б: выделить только полную фамилию.", "Прав Б: инициалы не размечаются как части; одной фамилии для качественного правила недостаточно.")
    add("partial_phone_not_annotated", "Фрагмент телефона вне MVP", [f"Я {FIO}. Последние цифры моего телефона — 0001."],
        [(1, FIO, "fio", "p1")], None, "А: выделить 0001 как телефон. Б: полного телефона нет.", "Прав Б: неполные типы не размечаются.")
    add("single_name_phone", "Одна часть имени и телефон", [f"Я Тестимир. Мой телефон {PHONE}."],
        [(1, "Тестимир", "first_name", "p1"), (1, PHONE, "phone", "p1")], None,
        "А: любое имя с контактом достаточно. Б: нужны две разные части.", "Прав Б для качественного правила; неподтверждённую национальную частоту имени не подставляем.")
    add("email_is_not_name_evidence", "Имя внутри email", [f"Моя почта {EMAIL}."], [(1, EMAIL, "email", "p1")], None,
        "А: выделить имя из локальной части email. Б: выделить только email.", "Прав Б: имя не достраивается из строки контакта; пересекающихся спанов нет.")
    add("ip_identity_not_employer", "ФИО в обозначении ИП", [f"Я зарегистрирован как ИП {FIO}."], [(1, FIO, "fio", "p1")], None,
        "А: ФИО и организация ИП дают два маркера. Б: это одно представление личности.",
        "Прав Б: префикс ИП не превращает то же ФИО в отдельного работодателя и не создаёт дополнительный факт.")
    add("address_components_not_separate", "Компоненты адреса", [f"Я живу по адресу: {ADDRESS}."], [(1, ADDRESS, "address", "p1")], None,
        "А: адрес и входящий в него город — два факта. Б: один полный адрес.", "Прав Б: размечается полный адрес без вложенных города/улицы/дома; адрес один не алертит.")
    add("phone_email", "Телефон и email", [f"Мой телефон {PHONE}.", f"Моя почта {EMAIL}."],
        [(1, PHONE, "phone", "p1"), (2, EMAIL, "email", "p1")], (2, "combination_rule"),
        "А: без имени нет алерта. Б: действует принятое контактное сочетание.", "Прав Б: правило телефона и email сохранено; это не универсальное правило любых двух оранжевых.")
    add("fio_correction", "Исправление части ФИО после алерта", [f"Меня зовут {FIO}.", f"Моя почта {EMAIL}.", "Исправление: моё имя Образимир."],
        [(1, FIO, "fio", "p1"), (2, EMAIL, "email", "p1"), (3, "Образимир", "first_name", "p1")], (2, "combination_rule"),
        "А: переписать первое имя в истории и сдвинуть алерт. Б: добавить новое упоминание.", "Прав Б: история неизменна, рабочее имя обновляется, первый алерт остаётся во втором сообщении.")
    add("repeated_name", "Повтор имени", ["Я Тестимир.", "Повторю: Тестимир.", f"Моя почта {EMAIL}."],
        [(1, "Тестимир", "first_name", "p1"), (2, "Тестимир", "first_name", "p1"), (3, EMAIL, "email", "p1")], None,
        "А: два упоминания имени образуют пару. Б: это одна часть.", "Прав Б: повтор одного типа и значения не создаёт ФИ/ИО/ФО.")
    add("fio_and_derived_sex", "Пол из формы имени", [f"Я {FIO}."], [(1, FIO, "fio", "p1")], None,
        "А: дополнительно разметить мужской пол по отчеству. Б: отдельного утверждения пола нет.", "Прав Б: производный пол не создаёт нового спана и оранжевого признака.")
    add("unknown_address_object", "Адрес банкомата", [f"Я {FIO}. Банкомат расположен по адресу: {ADDRESS}."],
        [(1, FIO, "fio", "p1"), (1, ADDRESS, "address", None)], None,
        "А: адрес относится к говорящему. Б: это адрес объекта.", "Прав Б: сущность сохраняется с неизвестной персоной, адрес проживания не назначается.")
    add("unknown_red", "Красный маркер с неизвестным владельцем", ["В документе указан СНИЛС 000-000-000 00."],
        [(1, "000-000-000 00", "snils", None)], (1, "red_rule"),
        "А: неизвестный владелец отменяет красное правило. Б: полный красный маркер достаточен.",
        "Прав Б по сохранённому красному правилу. Нули — заведомо синтетический суррогат для проверки политики, не тест валидатора СНИЛС.")
    add("no_retroactive_alert", "Не переносить алерт назад", ["Я Тестимир.", f"Мой телефон {PHONE}.", "Моя фамилия Тестов."],
        [(1, "Тестимир", "first_name", "p1"), (2, PHONE, "phone", "p1"), (3, "Тестов", "last_name", "p1")],
        (3, "combination_rule"), "А: поставить алерт на телефон, прочитав фамилию позже. Б: только на третьем сообщении.", "Прав Б: будущие сущности не используются при оценке прошлого префикса.")
    add("organization_stays_yellow", "ФИО и точный работодатель", [f"Я {FIO}. Работаю в ООО Макетный Пример."],
        [(1, FIO, "fio", "p1"), (1, "ООО Макетный Пример", "organization", "p1")], None,
        "А: работодатель завершает сочетание с ФИО. Б: организация теперь жёлтая.",
        "Прав Б по последнему уточнению: организация убрана из достаточного дополнения для ФИО/пар. Возможен другой отдельно обоснованный статистический путь.")
    add("three_parts_one_marker", "Три части не образуют три оранжевых маркера", ["Моя фамилия Тестов. Имя Тестимир. Отчество Макетович."],
        [(1, "Тестов", "last_name", "p1"), (1, "Тестимир", "first_name", "p1"), (1, "Макетович", "patronymic", "p1")], None,
        "А: есть ФИ, ИО и ФО — значит несколько оранжевых. Б: одно полное ФИО.", "Прав Б: объединяем три части в одно ФИО, самостоятельного алерта нет.")
    add("red_reason_priority", "Приоритет красного основания", [f"Я {FIO}. Мой телефон {PHONE}. СНИЛС 000-000-000 00."],
        [(1, FIO, "fio", "p1"), (1, PHONE, "phone", "p1"), (1, "000-000-000 00", "snils", "p1")], (1, "red_rule"),
        "А: причина — ФИО с телефоном. Б: в том же сообщении есть красный маркер.", "Прав Б для основной причины: красное основание имеет приоритет. Сочетание тоже выполнено; нули — синтетический суррогат документа.")
    add("unassigned_contacts", "Неизвестные контакты не образуют одно лицо", [f"Контакты приёмной: {PHONE}, {EMAIL}."],
        [(1, PHONE, "phone", None), (1, EMAIL, "email", None)], None,
        "А: null у двух контактов — одна персона. Б: принадлежность не установлена.",
        "Прав Б: null не объединяет сущности и не создаёт контактную пару одного человека.")
    add("explicit_example_with_alert", "Пример с собственной персоной и сработавшим правилом", [f"Учебный пример: клиент {FIO}; личная почта клиента {EMAIL}."],
        [(1, FIO, "fio", "p_example1"), (1, EMAIL, "email", "p_example1")], (1, "combination_rule"),
        "А: пометка примера должна запретить алерт. Б: отметка примера сохраняется отдельно от результата правила.",
        "Прав Б: is_example=true не отменяет сработавшее сочетание. Это позволяет исследовать срабатывания на вымышленных ситуациях отдельно.")
    add("no_unknown_reassignment", "Ранее неизвестный контакт не перепривязывается", [f"Контакт: {EMAIL}.", "Человек Б: Тестов Тестимир.", "Его контакт указан выше."],
        [(1, EMAIL, "email", None), (2, "Тестов", "last_name", "p_b"), (2, "Тестимир", "first_name", "p_b")], None,
        "А: привязать прежнюю почту по слову «его». Б: местоименную ссылку не обрабатывать.",
        "Прав Б: старая сущность остаётся с null, местоимения не размечаются, ретроспективной перепривязки нет.")
    add("unnamed_person_created", "Персона создана до появления имени", [f"Посетитель А: личный телефон {PHONE}.", "Посетитель А: Тестов Тестимир."],
        [(1, PHONE, "phone", "p_guest"), (2, "Тестов", "last_name", "p_guest"), (2, "Тестимир", "first_name", "p_guest")], (2, "combination_rule"),
        "А: сначала оставить телефон неизвестным, потом перепривязать. Б: сразу создать персону явно отдельного посетителя.",
        "Прав Б: персоне необязательно иметь имя. Оба сообщения явно описывают участника А; старые связи не изменяются.")
    for population in (99, 100, 101):
        policy = json.loads((ROOT / 'configs/annotation-estimator-policy.json').read_text(encoding='utf-8'))
        triggered = population < 100 if policy.get('candidate_comparison') == 'lt' else population <= 100
        name = f"с. Макетное-{population}"
        add(f"synthetic_population_{population}", f"Синтетический численный порог: {population}", [f"Я живу в {name}."],
            [(1, name, "geography", "p1")], (1, "candidate_count_rule") if triggered else None,
            "А и Б по-разному трактуют границу порога.", "Применяется оператор из рабочей конфигурации, без округления до сравнения. Численность вымышлена и задана только тестовым справочником.")

    geography = [{"id": f"synthetic-{n}", "label": f"с. Макетное-{n}", "region": "[СИНТЕТИЧЕСКИЙ РЕГИОН]",
                  "context": None, "kind": "settlement", "all": n, "male": n // 2, "female": n - n // 2}
                 for n in (99, 100, 101)]
    from annotation_contract import assess
    from annotation_calculator import Calculator
    calculator = Calculator()
    for row in geography:
        calculator.geo["rows"].append(row)
        calculator.geo_by_id[row["id"]] = row
    for case in cases:
        case["annotation"]["case_flags"] = assess(case["annotation"], calculator)["case_flags"]
    payload = {"synthetic_only": True, "version": "annotation-mvp-v2", "synthetic_geography": geography, "cases": cases}
    output = ROOT / "tests/fixtures/annotation-mvp-cases.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Синтетические примеры и разбор разногласий", "", "Версия annotation-mvp-v2. Все люди, адреса и контакты вымышлены. Код телефона намеренно несуществующий, домен `.invalid` тестовый. Это учебные сценарии политики, не проверка NER и не сведения о реальных людях.", "", "Воспроизведение:", "", "```powershell", "python -X utf8 tools/build_annotation_mvp_examples.py", "python -X utf8 tools/annotation_contract.py --check-cases tests/fixtures/annotation-mvp-cases.json", "```", "", "Границы в машинном файле вычислены как индексы Unicode code points, `[start, end)`. ФИО не является сущностью: везде выделяются отдельные Ф/И/О. `null` у персоны означает неизвестную принадлежность; такие строки не объединяются в одно лицо.", "", "Флаги примера и неуверенной разметки относятся ко всему кейсу. Синтетическое происхождение датасета не означает, что в тексте каждого диалога описан учебный пример: is_example отмечает именно контекст текста. Пример не подавляет правило алерта.", "", "Если алерт не возник, это означает отсутствие сработавшего достаточного правила. При неизвестной статистике весь кейс отмечен как неуверенная разметка, требующая уточнения ТЗ.", ""]
    for case in cases:
        lines += ["## " + case["id"] + ": " + case["title"], ""]
        for message in case["annotation"]["messages"]:
            lines.append(f"- **{message['id']}**: {message['text']}")
        lines += ["", "| Сущность | Сообщение | Границы | Тип | Персона |", "|---|---|---|---|---|"]
        texts = {m["id"]: m["text"] for m in case["annotation"]["messages"]}
        for e in case["annotation"]["entities"]:
            lines.append(f"| {texts[e['message_id']][e['start']:e['end']]} | {e['message_id']} | [{e['start']}, {e['end']}) | {e['type']} | {e['person_id'] or 'неизвестно'} |")
        expected = case["expected_first_alert"]
        lines += ["", "**Первый алерт:** " + (expected["message_id"] + ", `" + expected["reason"] + "`." if expected else "не возникает по достаточным правилам."), "", "**Разногласие:** " + case["disagreement"], "", "**Разбор:** " + case["resolution"], ""]
        lines += ["**Флаги кейса:** `" + json.dumps(case["annotation"]["case_flags"], ensure_ascii=False) + "`.", ""]
    (ROOT / "docs/data/annotation-synthetic-examples.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"synthetic_cases": len(cases), "fixture": str(output.relative_to(ROOT)), "generated_docs": "docs/data/annotation-synthetic-examples.md"}))


if __name__ == "__main__":
    build()
