"""Материализовать синтетические сессии и сохранённую разметку LLM текущего диалога.

Это воспроизведение сохранённого первого прохода, не новый вызов модели.
"""
import argparse
import hashlib
import json
from pathlib import Path
from annotation_contract import assess, validate
from annotation_calculator import Calculator
from export_annotation_context import build_context

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/experiments/llm-annotation-pilot-2026-11-18"

# Сессии вымышлены. Разметку ниже выполнила LLM в интерактивном диалоге.
# Числа документов — нулевые суррогаты; контакты заведомо нерабочие.
CASES = [
    ("pair_then_email", ["Здравствуйте! Я Макетов Образимир.",
       "Хочу уточнить статус заявки.", "Моя личная почта — obrazimir@example.invalid."],
     [(1, "Макетов", "last_name", "p1"), (1, "Образимир", "first_name", "p1"),
      (3, "obrazimir@example.invalid", "email", "p1")], False, None, ("m3", "combination_rule")),
    ("full_name_only", ["Моя фамилия Образцов, имя Тестимир, отчество Макетович.",
       "Проверьте, пожалуйста, правильно ли заполнено обращение."],
     [(1, "Образцов", "last_name", "p1"), (1, "Тестимир", "first_name", "p1"),
      (1, "Макетович", "patronymic", "p1")], False, "insufficient_statistics", None),
    ("office_phone", ["Я Образцов Тестимир Макетович.",
       "У справочной службы телефон +7 (000) 000-00-02. Это общий контакт службы."],
     [(1, "Образцов", "last_name", "p1"), (1, "Тестимир", "first_name", "p1"),
      (1, "Макетович", "patronymic", "p1"), (2, "+7 (000) 000-00-02", "phone", None)],
     False, "insufficient_statistics", None),
    ("two_people_contacts", ["Посетитель А: мой личный телефон +7 (000) 000-00-03.",
       "Посетитель Б: моя личная почта visitor-b@example.invalid."],
     [(1, "+7 (000) 000-00-03", "phone", "p1"), (2, "visitor-b@example.invalid", "email", "p2")],
     False, None, None),
    ("same_person_contacts", ["Посетитель А: мой личный телефон +7 (000) 000-00-04.",
       "Посетитель А: моя личная почта visitor-a@example.invalid."],
     [(1, "+7 (000) 000-00-04", "phone", "p1"), (2, "visitor-a@example.invalid", "email", "p1")],
     False, None, ("m2", "combination_rule")),
    ("initials_and_masks", ["Я Макетов О. Т. Телефон пока укажу частично: +7 *** ***-00-05.",
       "Email тоже скрыт: o***@***."], [(1, "Макетов", "last_name", "p1")],
     False, "insufficient_statistics", None),
    ("red_document", ["Подскажите, какие сведения нужны для заявки?",
       "Мой СНИЛС: 000-000-000 00."], [(2, "000-000-000 00", "snils", "p1")],
     False, None, ("m2", "red_rule")),
    ("fictional_person", ["Разберём вымышленную ситуацию. Клиент А: фамилия Тестов, имя Макетимир.",
       "В этой ситуации личная почта клиента А — client-a@example.invalid."],
     [(1, "Тестов", "last_name", "p1"), (1, "Макетимир", "first_name", "p1"),
      (2, "client-a@example.invalid", "email", "p1")], True, None, ("m2", "combination_rule")),
    ("residence_without_name", ["Мой адрес проживания: г. Макетоград, ул. Образцовая, д. 7.",
       "Квартиру пока не указываю."],
     [(1, "г. Макетоград, ул. Образцовая, д. 7", "address", "p1")], False, None, None),
    ("correction_after_alert", ["Мои имя и отчество — Тестимир Макетович.",
       "Моя личная почта testimir@example.invalid.", "Исправляю имя: я Образимир."],
     [(1, "Тестимир", "first_name", "p1"), (1, "Макетович", "patronymic", "p1"),
      (2, "testimir@example.invalid", "email", "p1"), (3, "Образимир", "first_name", "p1")],
     False, None, ("m2", "combination_rule")),
]


def build(output, policy_only=False):
    output.mkdir(parents=True, exist_ok=True)
    calculator = Calculator(policy_only=policy_only)
    sessions, annotations, checks = [], [], []
    for key, texts, labels, example, uncertain, expected in CASES:
        raw = {"session_id": "pilot-" + key,
               "messages": [{"id": f"m{i}", "text": t} for i,t in enumerate(texts,1)]}
        doc = {**raw, "schema_version": "annotation-mvp-v2", "entities": [],
               "case_flags": {"is_example": example, "annotation_uncertain": uncertain is not None,
                              "clarification_reason": uncertain}}
        for i,(number,value,kind,person) in enumerate(labels,1):
            text = texts[number-1]
            if text.count(value) != 1:
                raise ValueError("Нужна однозначная привязка выделенного фрагмента")
            start = text.index(value)
            doc["entities"].append(dict(id=f"e{i}", message_id=f"m{number}", start=start,
                                        end=start+len(value), type=kind, person_id=person))
        validate(doc)
        result = assess(doc, calculator)
        first = result["first_alert"]
        actual = (first["message_id"],first["reason"]) if first else None
        checks.append({"session_id": raw["session_id"], "valid": True,
            "expected_first_alert": expected, "actual_first_alert": actual,
            "rule_check_passed": expected == actual, "assessment": result,
            "human_review_status": "pending"})
        sessions.append(raw)
        annotations.append(doc)
    for name, content in [("sessions.json", {"synthetic_only": True, "sessions": sessions}),
        ("in-session-annotations.json", {"provenance": "LLM annotations authored in current interactive session; offsets resolved from exact selected text; not an external API run", "annotations": annotations}),
        ("review.json", {"checks": checks, "quality_metrics": None,
                          "limitation": "Rule smoke checks on authored examples; no independent human gold"})]:
        (output/name).write_text(json.dumps(content,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    prompt_paths = [ROOT/"docs/data"/name for name in ("llm-annotation-prompt.md", "annotation-class-fields.md", "schema.md")]
    manifest = {"annotation_source": "current_interactive_llm_session", "external_api_requests": 0,
        "reference_mode": "policy_only" if policy_only else "local_references",
        "context_capture": "at_materialization_not_independent_model_call",
        "policy_sha256": build_context()["policy_sha256"],
        "model_identifier": "not independently recorded by local API", "synthetic_only": True,
        "sessions": len(sessions), "entities": sum(len(d["entities"]) for d in annotations),
        "checks_passed": sum(c["rule_check_passed"] for c in checks),
        "prompt_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in prompt_paths},
        "sessions_sha256": hashlib.sha256((output/"sessions.json").read_bytes()).hexdigest()}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False))
    if not all(c["rule_check_passed"] for c in checks):
        raise ValueError("Сохранённое ожидание расходится с обработчиком; см. review.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--policy-only", action="store_true", help="Без локальных статистических справочников")
    args = parser.parse_args()
    build(args.output, policy_only=args.policy_only)
