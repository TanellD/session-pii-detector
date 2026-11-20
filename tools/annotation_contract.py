"""Минимальная разметка MVP: границы, тип и персона; проверка синтетических сценариев."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

if __package__:
    from .annotation_calculator import Calculator, RED, TYPES
else:
    from annotation_calculator import Calculator, RED, TYPES

VERSION = "annotation-mvp-v2"
LEGACY_VERSION = "annotation-mvp-v1"
ANNOTATION_TYPES = TYPES - {"fio", "relationship"}
CLARIFICATION_REASONS = {"insufficient_statistics", "specification_gap", "person_attribution"}


def validate(document):
    if not isinstance(document, dict) or document.get("schema_version") not in {VERSION, LEGACY_VERSION}:
        raise ValueError("Неверная версия или поля сессии")
    fields = {"schema_version", "session_id", "messages", "entities"}
    modern = document["schema_version"] == VERSION
    if modern:
        fields.add("case_flags")
    if set(document) != fields:
        raise ValueError("Неверные поля сессии")
    if modern:
        flags = document["case_flags"]
        if not isinstance(flags, dict) or set(flags) != {"is_example", "annotation_uncertain", "clarification_reason"}:
            raise ValueError("Нужны флаги примера и неуверенной разметки на уровне кейса")
        if type(flags["is_example"]) is not bool or type(flags["annotation_uncertain"]) is not bool:
            raise ValueError("Флаги кейса должны быть логическими")
        if flags["annotation_uncertain"]:
            if flags["clarification_reason"] not in CLARIFICATION_REASONS:
                raise ValueError("Нужна причина неуверенной разметки кейса")
        elif flags["clarification_reason"] is not None:
            raise ValueError("У уверенного кейса причина уточнения должна быть null")
    if not isinstance(document["session_id"], str) or not document["session_id"].strip():
        raise ValueError("Нужен session_id")
    if not isinstance(document["messages"], list) or not isinstance(document["entities"], list):
        raise ValueError("Сообщения и сущности должны быть списками")
    messages = {}
    for message in document["messages"]:
        if not isinstance(message, dict) or set(message) != {"id", "text"} or not all(isinstance(message[k], str) for k in ("id", "text")):
            raise ValueError("У сообщения нужны только id и text")
        if not message["id"].strip() or message["id"] in messages:
            raise ValueError("Идентификаторы сообщений должны быть уникальными")
        messages[message["id"]] = message["text"]
    ids, spans = set(), defaultdict(list)
    for entity in document["entities"]:
        if not isinstance(entity, dict) or set(entity) != {"id", "message_id", "start", "end", "type", "person_id"}:
            raise ValueError("У сущности нужны только id, message_id, start, end, type, person_id")
        if not isinstance(entity["id"], str) or not entity["id"].strip() or entity["id"] in ids:
            raise ValueError("Идентификаторы сущностей должны быть уникальными")
        ids.add(entity["id"])
        if (not isinstance(entity["message_id"], str) or not isinstance(entity["type"], str)
                or entity["message_id"] not in messages or entity["type"] not in (ANNOTATION_TYPES if modern else TYPES)):
            raise ValueError("Неизвестное сообщение или тип сущности")
        person = entity["person_id"]
        if person is not None and (not isinstance(person, str) or not person.strip()):
            raise ValueError("person_id должен быть непустой строкой или null")
        start, end = entity["start"], entity["end"]
        text = messages[entity["message_id"]]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("Неверные границы сущности")
        value = text[start:end]
        if value != value.strip():
            raise ValueError("Границы не должны включать внешние пробелы")
        if modern and value.split()[0].casefold() in {"в", "во", "на", "из", "с", "со", "к", "ко", "у", "от", "до", "по", "для", "о", "об", "при"}:
            raise ValueError("Предлог перед сущностью не входит в её границы")
        if any(start < other_end and other_start < end for other_start, other_end in spans[entity["message_id"]]):
            raise ValueError("Перекрывающиеся сущности не допускаются в минимальном MVP")
        spans[entity["message_id"]].append((start, end))
        if entity["type"] in {"first_name", "last_name", "patronymic", "fio"}:
            parts = value.split()
            if (not parts or (entity["type"] == "fio" and len(parts) != 3)
                    or any(len(p) < 2 or any(c in p for c in ".*…_") for p in parts)):
                raise ValueError("Инициалы, маски и неполные части имени вне MVP")
        if entity["type"] == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Нужен полный email")
        if entity["type"] == "phone" and len(re.sub(r"\D", "", value)) != 11:
            raise ValueError("Нужен полный телефон с кодом страны")
        if entity["type"] == "age" and not re.fullmatch(r"(?:0|[1-9]\d{0,2})(?:\s+(?:год|года|лет))?", value):
            raise ValueError("Нужен явно указанный возраст в полных годах; интервалы и даты рождения не входят в этот тип")
    return messages


def to_facts(document, calculator):
    messages = validate(document)
    order = {m["id"]: i for i, m in enumerate(document["messages"])}
    entities = sorted(document["entities"], key=lambda e: (order[e["message_id"]], e["start"], e["end"]))
    facts = []
    for entity in entities:
        value = messages[entity["message_id"]][entity["start"]:entity["end"]]
        kind = entity["type"]
        fact = {"type": kind, "value": value, "person_id": entity["person_id"],
                "entity_id": entity["id"], "message_id": entity["message_id"], "complete": True}
        if kind in {"phone", "email"}:
            fact["usage"] = "personal" if entity["person_id"] is not None else "unknown"
        elif kind == "organization":
            fact["exact"] = entity["person_id"] is not None
        elif kind == "address":
            fact["completeness"] = "full"
        elif kind == "geography":
            matches = [r for r in calculator.geo["rows"] if r["label"].casefold() == value.casefold()]
            if len(matches) == 1:
                fact["geography_id"] = matches[0]["id"]
        elif kind == "sex":
            fact["value"] = {"мужчина": "male", "мужской": "male", "женщина": "female", "женский": "female"}.get(value.casefold(), value)
        facts.append(fact)
    return facts


def assess(document, calculator=None, address_enrichment=None):
    calculator = calculator or Calculator()
    facts = to_facts(document, calculator)
    if address_enrichment is not None:
        from annotation_address_enrichment import apply_enrichment
        facts = apply_enrichment(document, calculator, facts, address_enrichment)
    by_message, people, history, unknown = defaultdict(list), defaultdict(list), [], []
    for fact in facts:
        by_message[fact["message_id"]].append(fact)
    first_alert = None
    for index, message in enumerate(document["messages"], 1):
        current = by_message[message["id"]]
        for fact in current:
            if fact["person_id"] is None:
                unknown.append(fact)
            else:
                people[fact["person_id"]].append(fact)
        # Проверяем завершённое сообщение: порядок спанов внутри него
        # не должен создавать отдельный исторический алерт до исправления.
        assessments = {person: calculator.person(rows) for person, rows in people.items()}
        unknown_red = [f for f in unknown if f["type"] in RED]
        red = bool(unknown_red) or any(r["alert_reason"] == "red_rule" for r in assessments.values())
        combination = any(r["alert_reason"] == "combination_rule" for r in assessments.values())
        numerical = any(r["alert_reason"] == "candidate_count_rule" for r in assessments.values())
        reason = "red_rule" if red else "combination_rule" if combination else "candidate_count_rule" if numerical else None
        if reason and first_alert is None:
            if reason == "red_rule":
                evidence = [f["entity_id"] for rows in people.values() for f in rows if f["type"] in RED]
                evidence += [f["entity_id"] for f in unknown_red]
            else:
                # Контекст персоны для воспроизведения, не минимальный набор доказательств.
                evidence = [f["entity_id"] for p, rows in people.items()
                            if assessments[p]["alert_reason"] == reason for f in rows]
            first_alert = {"message_id": message["id"], "message_index": index,
                           "reason": reason, "context_entity_ids": evidence}
        # Неизвестная принадлежность хранится в person_id=null. Это не
        # неопределённость вердикта: такие факты не объединяются с персонами.
        unlinked_pending = bool(unknown) and calculator.policy.get("annotation_semantics", {}).get(
            "unlinked_entities_trigger_verdict_uncertainty", False)
        pending = unlinked_pending or any(r["alert"] is None for r in assessments.values())
        history.append({"message_id": message["id"], "rule_alert": reason is not None,
                        "assessment": "triggered" if reason else "insufficient_data" if pending else "not_triggered",
                        "annotation_uncertain": bool(pending and not reason),
                        "clarification_reason": "insufficient_statistics" if pending and not reason else None,
                        "reason": reason})
    flags = dict(document.get("case_flags", {"is_example": False, "annotation_uncertain": False, "clarification_reason": None}))
    # Отметки прошлых шагов и исходная ручная отметка сохраняются.
    # Неуверенность не является алертом и не исключает кейс из бинарного расчёта.
    if any(step["annotation_uncertain"] for step in history):
        flags["annotation_uncertain"] = True
        if flags["clarification_reason"] is None:
            flags["clarification_reason"] = "insufficient_statistics"
    return {"schema_version": document["schema_version"], "session_id": document["session_id"], "first_alert": first_alert,
            "policy_version": calculator.policy["version"], "taxonomy_version": calculator.policy["taxonomy_version"],
            "case_flags": flags,
            "session_alert": first_alert is not None, "messages": history,
            "people": {p: calculator.person(rows) for p, rows in people.items()},
            "unknown_entity_ids": [f["entity_id"] for f in unknown], "history_unchanged": True}


def check_cases(path, calculator=None):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    calculator = calculator or Calculator()
    for row in payload.get("synthetic_geography", []):
        calculator.geo["rows"].append(row)
        calculator.geo_by_id[row["id"]] = row
    for case in payload["cases"]:
        result = assess(case["annotation"], calculator)
        actual = {"message_id": result["first_alert"]["message_id"], "reason": result["first_alert"]["reason"]} if result["first_alert"] else None
        if actual != case["expected_first_alert"]:
            raise ValueError("Синтетический сценарий не прошёл: " + case["id"])
    return {"status": "passed", "synthetic_cases": len(payload["cases"]), "schema_version": VERSION,
            "scope": "annotation rule regression; not detector quality or real population validation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-only", action="store_true", help="Без локальных справочников; отсутствующие оценки неизвестны")
    parser.add_argument("--address-enrichment", type=Path, help="Сохранённый адресный этап; без обращений к сети")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-cases", type=Path)
    group.add_argument("--input", type=Path)
    args = parser.parse_args()
    calculator = Calculator(policy_only=args.policy_only)
    if args.check_cases:
        result = check_cases(args.check_cases, calculator)
    else:
        enrichment = json.loads(args.address_enrichment.read_text(encoding="utf-8")) if args.address_enrichment else None
        result = assess(json.loads(args.input.read_text(encoding="utf-8")), calculator, address_enrichment=enrichment)
    # Никогда не печатаем исходные значения сущностей даже при реальном входе.
    if args.input:
        result = {k: v for k, v in result.items() if k != "people"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
