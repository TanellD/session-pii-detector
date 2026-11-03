"""Small diagnostic rules; checksum validity is not proof of an assigned identity."""

import re


def digits(value):
    return "".join(c for c in value if c.isdigit())


def valid_snils(value):
    value = digits(value)
    if len(value) != 11:
        return False
    checksum = sum(int(c) * w for c, w in zip(value[:9], range(9, 0, -1)))
    checksum = checksum if checksum < 100 else checksum % 101 % 100
    return checksum == int(value[-2:])


def valid_inn(value):
    value = digits(value)
    if len(value) != 12:
        return False
    first = (
        sum(int(c) * w for c, w in zip(value[:10], [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]))
        % 11
        % 10
    )
    second = (
        sum(int(c) * w for c, w in zip(value[:11], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]))
        % 11
        % 10
    )
    return first == int(value[10]) and second == int(value[11])


def valid_luhn(value):
    value = digits(value)
    if not 13 <= len(value) <= 19:
        return False
    total = 0
    for i, c in enumerate(reversed(value)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


PATTERNS = [
    (
        "EMAIL",
        re.compile(r"(?P<value>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?!\w)"),
        None,
    ),
    (
        "PHONE",
        re.compile(
            r"(?<!\d)(?P<value>(?:\+7|8)[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2})(?!\d)"
        ),
        None,
    ),
    (
        "SNILS",
        re.compile(r"(?<!\d)(?P<value>\d{3}-\d{3}-\d{3}[ ]\d{2})(?!\d)"),
        valid_snils,
    ),
    ("INN", re.compile(r"\bИНН\s*[:№]?\s*(?P<value>\d{12})(?!\d)", re.I), valid_inn),
    (
        "CARD",
        re.compile(
            r"\bкарт[аы]\s*[:№]?\s*(?P<value>\d{4}(?:[ -]?\d{4}){3})(?!\d)", re.I
        ),
        valid_luhn,
    ),
    (
        "PASSPORT",
        re.compile(
            r"\bпаспорт[а]?\s*[:№]?\s*(?P<value>\d{2}[ ]?\d{2}[ ]\d{6})(?!\d)", re.I
        ),
        None,
    ),
]


def predict(text):
    result = []
    for kind, pattern, validator in PATTERNS:
        for match in pattern.finditer(text):
            if validator is None or validator(match.group("value")):
                result.append(
                    {
                        "type": kind,
                        "start": match.start("value"),
                        "end": match.end("value"),
                        "score": None,
                        "source": "regex",
                    }
                )
    return result


def merge(rule_entities, model_entities):
    """Rules take precedence only over overlapping spans of the same type."""
    result = list(rule_entities)
    for entity in model_entities:
        if not any(
            e["type"] == entity["type"]
            and max(e["start"], entity["start"]) < min(e["end"], entity["end"])
            for e in result
        ):
            result.append(entity)
    return sorted(result, key=lambda e: (e["start"], e["end"], e["type"]))
