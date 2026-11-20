"""Rules that add project types and resolve deterministic conflicts."""

import re

from .regex_detector import valid_inn

RULES_VERSION = "pii_smoke_rules-v1"

# Pronouns and possessive forms that GLiNER sometimes tags as PERSON.
# They are not attributes of a person and must not become PERSON mentions.
PRONOUNS = {
    "я", "ты", "он", "она", "оно", "мы", "вы",
    "они", "меня", "тебя", "его", "её", "нас",
    "вас", "их", "мне", "тебе", "ему", "ей",
    "нам", "вам", "им", "мой", "моя", "моё",
    "мои", "твой", "твоя", "твоё", "твои",
    "наш", "наша", "наше", "наши", "ваш",
    "ваша", "ваше", "ваши",
}


def overlap(a, b):
    return max(a["start"], b["start"]) < min(a["end"], b["end"])


def split_person(message, entity):
    start, end = entity["start"], entity["end"]
    fragment = message[start:end]

    surname_context = re.search(r"(?:фамилия|фамилии)\s+(.+?)(?:[.,]|$)", message, re.I)
    patronymic_context = re.search(r"(?:отчество|отчества)\s+([^ .,]+)", message, re.I)

    if surname_context:
        value = surname_context.group(1).strip()
        pos = message.find(value, start)
        if pos >= 0:
            return [
                {**entity, "type": "LAST_NAME", "start": pos, "end": pos + len(value)}
            ]
    if patronymic_context:
        value = patronymic_context.group(1).strip()
        pos = message.find(value, start)
        if pos >= 0:
            return [
                {**entity, "type": "PATRONYMIC", "start": pos, "end": pos + len(value)}
            ]

    tokens = fragment.split()
    if len(tokens) == 3:
        pos = start
        parts = []
        for kind in ("FIRST_NAME", "PATRONYMIC", "LAST_NAME"):
            token = tokens[len(parts)]
            pos = message.find(token, pos)
            parts.append(
                {
                    **entity,
                    "type": kind,
                    "start": pos,
                    "end": pos + len(token),
                    "text": token,
                }
            )
            pos += len(token)
        return parts

    if len(tokens) == 2 and "зовут" in message[:start]:
        pos = start
        parts = []
        for kind, token in (("FIRST_NAME", tokens[0]), ("LAST_NAME", tokens[1])):
            pos = message.find(token, pos)
            parts.append(
                {
                    **entity,
                    "type": kind,
                    "start": pos,
                    "end": pos + len(token),
                    "text": token,
                }
            )
            pos += len(token)
        return parts

    if len(tokens) == 1:
        prefix = message[:start].lower()
        kind = "FIRST_NAME" if "зовут" in prefix else "PERSON"
        return [{**entity, "type": kind}]
    return [{**entity, "type": "FULL_NAME"}]


def merge_organizations(message, entities):
    """Rule-based organization merger.

    Detected by regex (ООО/АО/ИП), so provenance is regex, not GLiNER.
    """
    result = list(entities)
    for match in re.finditer(r"(?i)(?:ООО|АО|ИП)\s*[«\"]([^»\"]+)[»\"]", message):
        span = {
            "type": "ORGANIZATION",
            "start": match.start(),
            "end": match.end(),
            "score": None,
            "confidence_kind": "none",
            "source": "regex",
            "detector_version": RULES_VERSION,
        }
        result = [
            e for e in result if not overlap(e, span) or e["type"] != "ORGANIZATION"
        ]
        result.append(span)
    return result


def resolve_conflicts(message, entities):
    result = []
    inn_entities = [e for e in entities if e["type"] == "INN"]
    suppressed_phones = []
    for inn in inn_entities:
        value = message[inn["start"] : inn["end"]]
        if len("".join(c for c in value if c.isdigit())) == 12 and valid_inn(value):
            for phone in entities:
                if phone["type"] == "PHONE" and overlap(inn, phone):
                    suppressed_phones.append(phone)

    for entity in entities:
        if entity["type"] == "PERSON":
            value = message[entity["start"] : entity["end"]].strip().lower()
            if value in PRONOUNS:
                continue
        if entity["type"] == "PHONE":
            value = message[entity["start"] : entity["end"]]
            if entity["source"] == "gliner" and not any(c.isdigit() for c in value):
                continue
            if any(overlap(entity, p) for p in suppressed_phones):
                continue
        if any(e["type"] == entity["type"] and overlap(e, entity) for e in result):
            continue
        result.append(entity)
    return sorted(result, key=lambda e: (e["start"], e["end"], e["type"]))


def validate_spans(message, entities):
    for entity in entities:
        if not 0 <= entity["start"] < entity["end"] <= len(message):
            raise ValueError(
                f"Invalid span {entity['type']} [{entity['start']}:{entity['end']}] "
                f"for message of length {len(message)}"
            )


def apply_rules(message, entities, detector_version=None):
    validate_spans(message, entities)
    result = []
    for entity in entities:
        if entity["type"] == "PERSON":
            result.extend(split_person(message, entity))
        else:
            result.append(entity)
    result = merge_organizations(message, result)
    return resolve_conflicts(message, result)
