"""Deterministic synthetic fixture generator. Generated data are never committed."""

import argparse
import hashlib
import json
from pathlib import Path

from .regex_detector import valid_inn, valid_luhn, valid_snils


def build():
    snils_base = "000000123"
    snils = next(
        snils_base + f"{n:02d}"
        for n in range(100)
        if valid_snils(snils_base + f"{n:02d}")
    )
    snils = f"{snils[:3]}-{snils[3:6]}-{snils[6:9]} {snils[9:]}"
    inn = next(
        "0000001234" + f"{n:02d}"
        for n in range(100)
        if valid_inn("0000001234" + f"{n:02d}")
    )
    card = next(
        "000000000000001" + str(n)
        for n in range(10)
        if valid_luhn("000000000000001" + str(n))
    )
    bad_snils = snils[:-1] + str((int(snils[-1]) + 1) % 10)
    examples = [
        ("full_name", "Меня зовут Лада Макетова.", [("PERSON", "Лада Макетова")]),
        ("first_name", "Меня зовут Лада.", [("PERSON", "Лада")]),
        ("surname", "Моя фамилия Макетова.", [("PERSON", "Макетова")]),
        ("phone", "Мой телефон +7 000 000-00-01.", [("PHONE", "+7 000 000-00-01")]),
        ("email", "Пишите на lada@example.com.", [("EMAIL", "lada@example.com")]),
        (
            "address",
            "Мой адрес: ул. Макетная, д. 7.",
            [("ADDRESS", "ул. Макетная, д. 7")],
        ),
        (
            "birth_date",
            "Дата моего рождения — 14.02.1991.",
            [("DATE_OF_BIRTH", "14.02.1991")],
        ),
        ("age", "Мне 34 года.", [("AGE", "34 года")]),
        ("city", "Я живу в Кимрах.", [("CITY", "Кимрах")]),
        ("organization", "Я работаю в ООО «Макет».", [("ORGANIZATION", "ООО «Макет»")]),
        ("occupation", "Я работаю инженером.", [("OCCUPATION", "инженером")]),
        ("relationship", "Моя мама скоро приедет.", [("RELATIONSHIP", "мама")]),
        ("gender", "Я женщина.", [("GENDER", "женщина")]),
        ("health", "У меня диабет.", [("SPECIAL_CATEGORY", "диабет")]),
        ("religion", "Моя религия — буддизм.", [("SPECIAL_CATEGORY", "буддизм")]),
        (
            "politics",
            "Я придерживаюсь социалистических взглядов.",
            [("SPECIAL_CATEGORY", "социалистических взглядов")],
        ),
        (
            "nationality",
            "По национальности я татарка.",
            [("SPECIAL_CATEGORY", "татарка")],
        ),
        ("philosophy", "Я сторонница стоицизма.", [("SPECIAL_CATEGORY", "стоицизма")]),
        (
            "intimacy",
            "Это сведения о моей интимной жизни.",
            [("SPECIAL_CATEGORY", "интимной жизни")],
        ),
        ("conviction", "У меня была судимость.", [("SPECIAL_CATEGORY", "судимость")]),
        ("snils", f"Тестовый СНИЛС: {snils}.", [("SNILS", snils)]),
        ("inn", f"Тестовый ИНН: {inn}.", [("INN", inn)]),
        ("card", f"Тестовая карта: {card}.", [("CARD", card)]),
        ("passport", "Тестовый паспорт: 00 00 000123.", [("PASSPORT", "00 00 000123")]),
        ("bad_checksum", f"Невалидная последовательность: {bad_snils}.", []),
        ("ordinary_numbers", "Версия 4510, номер задачи 123456.", []),
        (
            "code",
            'В конфиге: {"user_email": "fixture@example.com"}.',
            [("EMAIL", "fixture@example.com")],
        ),
        (
            "two_people",
            "Лада Макетова работает в ООО «Макет», а Олег Тестов — инженер.",
            [
                ("PERSON", "Лада Макетова"),
                ("ORGANIZATION", "ООО «Макет»"),
                ("PERSON", "Олег Тестов"),
                ("OCCUPATION", "инженер"),
            ],
        ),
        (
            "correction",
            "Фамилия не Макетова, а Тестова.",
            [("PERSON", "Макетова"), ("PERSON", "Тестова")],
        ),
        ("negative", "Покажи прогноз погоды на завтра.", []),
    ]
    rows = []
    for case_id, text, annotations in examples:
        expected = [
            {
                "type": kind,
                "start": text.index(value),
                "end": text.index(value) + len(value),
            }
            for kind, value in annotations
        ]
        rows.append({"id": case_id, "text": text, "expected": expected})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("data/task12-smoke-v1.json")
    )
    args = parser.parse_args()
    content = (
        json.dumps(
            {"version": "task12-smoke-v1", "synthetic": True, "cases": build()},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        json.dumps(
            {"cases": len(build()), "sha256": hashlib.sha256(content).hexdigest()},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
