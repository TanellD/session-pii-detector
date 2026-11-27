"""Создать новый синтетический набор и эталон текущей LLM для сравнения моделей."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from annotation_calculator import Calculator
from annotation_contract import assess, validate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data/experiments/annotation-comparison-2026-11-19"

# Всё вымышлено; номера нулевые, почта .invalid. Ожидания заданы автором,
# а не получены от сравниваемых моделей. Данные создаются только вне Git.
# (ключ, описание, сообщения, (номер сообщения, фрагмент, тип, персона), пример, ожидаемый первый алерт)
CASES = [
    ("clean", "Полностью чистая сессия", [
        "Здравствуйте! Как сделать текст письма вежливее?",
        "Начните с приветствия и коротко сформулируйте просьбу.",
        "Спасибо, добавлю благодарность в конце."], [], False, None),
    ("yellow_profile", "ФИО, пол, возраст, город и работодатель без оранжевых фактов", [
        "Моя фамилия Макетонов, имя Тестослав, отчество Образцович.",
        "В анкете мой пол: мужской. Мне 37 лет, я живу в Москве.",
        "Мой работодатель — ООО «Макетный Архив»."], [
        (1,"Макетонов","last_name","p1"), (1,"Тестослав","first_name","p1"),
        (1,"Образцович","patronymic","p1"), (2,"мужской","sex","p1"),
        (2,"37 лет","age","p1"), (2,"Москве","geography","p1"),
        (3,"ООО «Макетный Архив»","organization","p1")], False, None),
    ("fi_email", "ФИ по разным сообщениям и личная почта", [
        "Моя фамилия Синтетиков.", "Моё имя Макетимир.",
        "Моя личная почта — maketimir@example.invalid."], [
        (1,"Синтетиков","last_name","p1"), (2,"Макетимир","first_name","p1"),
        (3,"maketimir@example.invalid","email","p1")], False, ("m3","combination_rule")),
    ("io_phone", "ИО и полный личный телефон", [
        "Меня зовут Образимир Тестович.", "Можно отправить напоминание о встрече?",
        "Мой личный телефон: +7 (000) 000-00-11."], [
        (1,"Образимир","first_name","p1"), (1,"Тестович","patronymic","p1"),
        (3,"+7 (000) 000-00-11","phone","p1")], False, ("m3","combination_rule")),
    ("fo_address", "ФО и полный адрес проживания; без вложенных географических дублей", [
        "В анкете моя фамилия Примеронов, отчество Макетович.",
        "Имя в этой заявке не указываю.",
        "Я проживаю по адресу: г. Макетоград, ул. Синтетическая, д. 8, кв. 4."], [
        (1,"Примеронов","last_name","p1"), (1,"Макетович","patronymic","p1"),
        (3,"г. Макетоград, ул. Синтетическая, д. 8, кв. 4","address","p1")], False, ("m3","combination_rule")),
    ("contacts_correction", "Личные контакты без имени и исправление телефона", [
        "Посетитель А: мой личный телефон +7 (000) 000-00-12.",
        "Посетитель А: исправляю мой телефон на +7 (000) 000-00-13.",
        "Посетитель А: моя личная почта contact-a@example.invalid.",
        "Посетитель А: повторю почту: contact-a@example.invalid."], [
        (1,"+7 (000) 000-00-12","phone","p1"), (2,"+7 (000) 000-00-13","phone","p1"),
        (3,"contact-a@example.invalid","email","p1"), (4,"contact-a@example.invalid","email","p1")], False, ("m3","combination_rule")),
    ("separate_owners", "Разные владельцы и служебный контакт", [
        "Посетитель А: я Тестомиров Образослав.",
        "Посетитель Б: моя профессия — инженер, мой личный телефон +7 (000) 000-00-14.",
        "Общий email справочной службы: helpdesk@example.invalid. Это служебный контакт."], [
        (1,"Тестомиров","last_name","p1"), (1,"Образослав","first_name","p1"),
        (2,"инженер","occupation","p2"), (2,"+7 (000) 000-00-14","phone","p2"),
        (3,"helpdesk@example.invalid","email",None)], False, None),
    ("address_only", "Адрес без других личных признаков не алертит", [
        "Мой адрес проживания: г. Примероград, ул. Макетная, д. 3, кв. 2.",
        "Можно уточнить время доставки?", "Других сведений не добавляю."], [
        (1,"г. Примероград, ул. Макетная, д. 3, кв. 2","address","p1")], False, None),
    ("red_unknown_example", "Красные маркеры без владельца в явно учебном примере", [
        "Это учебный пример с вымышленными номерами, владельцы не указаны.",
        "СНИЛС: 000-000-000 00.", "Паспорт: 0000 000000.",
        "ИНН физического лица: 000000000000.", "Номер банковской карты: 0000 0000 0000 0000."], [
        (2,"000-000-000 00","snils",None), (3,"0000 000000","passport",None),
        (4,"000000000000","individual_inn",None), (5,"0000 0000 0000 0000","bank_card",None)], True, ("m2","red_rule")),
    ("single_name_and_words", "Одна часть имени с телефоном; слова, инициалы и маски исключаются", [
        "Моё полное имя — Тестолюб.", "Мой личный телефон +7 (000) 000-00-15.",
        "Мой возраст — тридцать семь лет. Телефон словами: плюс семь, ноль ноль ноль, ноль ноль ноль, ноль ноль, пятнадцать.",
        "Отчество сокращу до М.; почту пока скрою: user@…"], [
        (1,"Тестолюб","first_name","p1"), (2,"+7 (000) 000-00-15","phone","p1")], False, None),
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "data") or output.exists():
        parser.error("Нужен новый каталог внутри data/")
    calculator = Calculator()
    sessions, gold, checks, markdown = [], [], [], [
        "# Синтетические сессии для сравнения LLM", "",
        "Эталон составлен в текущей LLM-сессии. Проверка человеком ожидается.",
        "Все значения вымышлены; это отдельный тестовый набор, не случайная выборка.", ""]
    for key, title, texts, labels, example, expected in CASES:
        raw = {"session_id": "compare-" + key, "messages": [{"id": f"m{i}", "text": t} for i, t in enumerate(texts, 1)]}
        doc = {**raw, "schema_version": "annotation-mvp-v2", "entities": [],
               "case_flags": {"is_example": example, "annotation_uncertain": False, "clarification_reason": None}}
        for i, (m, quote, kind, person) in enumerate(labels, 1):
            if texts[m-1].count(quote) != 1:
                raise ValueError("Фрагмент должен иметь единственное точное вхождение")
            start = texts[m-1].index(quote)
            doc["entities"].append(dict(id=f"e{i}", message_id=f"m{m}", type=kind, person_id=person, start=start, end=start+len(quote)))
        validate(doc)
        result = assess(doc, calculator)
        actual = (result["first_alert"]["message_id"], result["first_alert"]["reason"]) if result["first_alert"] else None
        if actual != expected:
            raise ValueError(f"Несогласованность авторского ожидания и калькулятора: {key}; {actual}")
        # Историческую нехватку статистики префиксов сохраняет обработчик.
        doc["case_flags"] = result["case_flags"]
        checks.append({"session_id": raw["session_id"], "title": title, "expected_first_alert": expected,
                       "assessment": result})
        sessions.append(raw)
        gold.append(doc)
        markdown += [f"## {raw['session_id']}: {title}", ""]
        markdown += [f"**m{i}.** {t}\n" for i, t in enumerate(texts, 1)]
        markdown += [f"**Эталонный первый алерт:** {expected[0] + ' / ' + expected[1] if expected else 'нет'}.",
                     f"**Флаги после обработки:** `{json.dumps(doc['case_flags'], ensure_ascii=False)}`.", "",
                     "| Сообщение | Фрагмент | Класс | Персона |", "|---|---|---|---|"]
        markdown += [f"| m{m} | {q} | {t} | {p if p is not None else 'null'} |" for m, q, t, p in labels]
        if not labels:
            markdown += ["| — | Нет сущностей | — | — |"]
        markdown += [""]
    output.mkdir(parents=True)
    write(output / "sessions.json", {"synthetic_only": True, "sessions": sessions})
    write(output / "gold.json", {"annotations": gold})
    write(output / "gold-assessments.json", {"checks": checks})
    (output / "sessions-for-review.md").write_text("\n".join(markdown), encoding="utf-8")
    paths = [Path(__file__).resolve(), ROOT / "configs/annotation-estimator-policy.json",
             ROOT / "configs/annotation-classes.json", ROOT / "docs/data/annotation-guidelines.md",
             ROOT / "docs/data/llm-annotation-prompt.md", ROOT / "docs/kb/metrics.md"]
    write(output / "manifest.json", {"synthetic_only": True, "annotator": "current_interactive_llm_session",
        "human_review_status": "pending", "reference_status": "author_gold_for_small_diagnostic_not_independent_benchmark",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "policy_version": calculator.policy["version"],
        "hashes": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "sessions_sha256": hashlib.sha256((output / "sessions.json").read_bytes()).hexdigest()})
    print(json.dumps({"output": str(output), "sessions": len(sessions), "messages": sum(len(s['messages']) for s in sessions),
        "entities": sum(len(d['entities']) for d in gold), "positive_sessions": sum(c['expected_first_alert'] is not None for c in checks),
        "all_expectations_match_calculator": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
