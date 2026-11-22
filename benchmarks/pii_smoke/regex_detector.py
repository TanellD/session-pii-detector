"""Small diagnostic rules; checksum validity is not proof of an assigned identity."""

import re
from .phone_format import PHONE_BODY, valid_phone_span


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
            rf"(?<![0-9])(?P<value>{PHONE_BODY})(?![0-9])"
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


# ---------------------------------------------------------------------------
# Address chain. Covers:
#   г.Москва, ул.Тверская, д.12
#   г. Москва, ул. Тверская, д. 12
#   Москва, ул.Тверская, д.12
#   г.Москва, Тверская, 12          (street without marker, house without marker)
#   г.Москва, Тверская, 12, кв. 5
#   123456, г.Москва, ул.Тверская, д.12
#   Санкт-Петербург, Невский проспект, 28
# ---------------------------------------------------------------------------

_CITY = r"(?:[А-ЯЁ][а-яё-]+(?:-[А-ЯЁ][а-яё-]+)?)"
_CITY_MARKED = rf"(?:г\.\s*|город\s+)?{_CITY}"

_STREET_MARKERS = (
    r"(?:ул\.|улица|пр\.|пр-т|проспект|пер\.|переулок|"
    r"б-р|бульвар|ш\.|шоссе|пл\.|площадь|наб\.|набережная|"
    r"проезд|туп\.|тупик)"
)
_STREET = rf"{_STREET_MARKERS}\s*{_CITY}(?:\s+{_CITY})?"
_STREET_PLAIN = rf"{_CITY}(?:\s+{_CITY})?"  # «Тверская», «Невский проспект»

_HOUSE = r"(?:д\.\s*|дом\s+)?\d+[А-Яа-я]?(?:[/-]\d+)?"
_CORP = r"(?:корп\.|корпус|стр\.|строение)\s*\d+[А-Яа-я]?"
_FLAT = r"(?:кв\.|квартира)\s*\d+"
_INDEX = r"\d{6}"

ADDRESS_CHAIN_RE = re.compile(
    rf"(?:(?P<index>{_INDEX})\s*,\s*)?"
    rf"{_CITY_MARKED}"
    rf"\s*,\s*(?:{_STREET}|{_STREET_PLAIN})"
    rf"\s*,\s*{_HOUSE}"
    rf"(?:\s*,\s*{_CORP})?"
    rf"(?:\s*,\s*{_FLAT})?"
)


def predict_address_chains(text):
    """Find full address chains and return ADDRESS spans."""
    result = []
    for match in ADDRESS_CHAIN_RE.finditer(text):
        result.append(
            {
                "type": "ADDRESS",
                "start": match.start(),
                "end": match.end(),
                "score": None,
                "source": "regex",
                "detector_version": "pii_smoke_rules-v2",
            }
        )
    return result

# ---------------------------------------------------------------------------
# Initials / short name forms. Produced as PERSON spans and resolved later
# by adapter_rules.split_person into LAST_NAME / FIRST_NAME / PATRONYMIC.
#
# Covered:
#   Иванова И.И.        (surname first, two initials, space)
#   Иванова И. И.       (surname first, two initials, space inside)
#   И.И. Иванова        (initials first, two, space before surname)
#   И. И. Иванова       (initials first, two, space inside)
#   И.И.Иванова         (initials first, two, no space before surname)
#   И.Иванова           (initials first, one, no space before surname)
#   И. Иванова          (initials first, one, space before surname)
#   Иванова И.          (surname first, one initial)
# ---------------------------------------------------------------------------
_INITIAL = r"[А-ЯЁ]"
_SURNAME = r"[А-ЯЁ][а-яё-]+"

INITIALS_SURNAME_FIRST = re.compile(
    rf"\b(?P<surname>{_SURNAME})\s+(?P<first>{_INITIAL})\.\s*(?P<patronymic>{_INITIAL})\."
)

INITIALS_SURNAME_FIRST_ONE = re.compile(
    rf"\b(?P<surname>{_SURNAME})\s+(?P<first>{_INITIAL})\."
)

INITIALS_SURNAME_LAST = re.compile(
    rf"\b(?P<first>{_INITIAL})\.\s*(?P<patronymic>{_INITIAL})\.\s*(?P<surname>{_SURNAME})"
)

INITIALS_SURNAME_LAST_NO_SPACE = re.compile(
    rf"\b(?P<first>{_INITIAL})\.\s*(?P<patronymic>{_INITIAL})\.(?P<surname>{_SURNAME})"
)

INITIALS_ONE_SURNAME = re.compile(
    rf"\b(?P<first>{_INITIAL})\.\s*(?P<surname>{_SURNAME})"
)

INITIALS_ONE_SURNAME_NO_SPACE = re.compile(
    rf"\b(?P<first>{_INITIAL})\.(?P<surname>{_SURNAME})"
)


def predict_initials(text):
    """Find short name forms and return PERSON spans for later split."""
    result = []

    def add(match):
        result.append(
            {
                "type": "PERSON",
                "start": match.start(),
                "end": match.end(),
                "score": None,
                "source": "regex",
                "detector_version": "pii_smoke_rules-v2",
            }
        )

    for pattern in (
        INITIALS_SURNAME_FIRST,
        INITIALS_SURNAME_LAST,
        INITIALS_SURNAME_LAST_NO_SPACE,
    ):
        for match in pattern.finditer(text):
            add(match)

    for pattern in (
        INITIALS_SURNAME_FIRST_ONE,
        INITIALS_ONE_SURNAME,
        INITIALS_ONE_SURNAME_NO_SPACE,
    ):
        for match in pattern.finditer(text):
            add(match)

    result.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))
    deduped = []
    for entity in result:
        if not any(
            e["start"] <= entity["start"] and entity["end"] <= e["end"]
            for e in deduped
        ):
            deduped.append(entity)
    return deduped


def predict(text, *, require_valid_checksum=True):
    result = []
    for kind, pattern, validator in PATTERNS:
        for match in pattern.finditer(text):
            if kind == "PHONE" and not valid_phone_span(text, match.start("value"), match.end("value")):
                continue
            if validator is None or not require_valid_checksum or validator(match.group("value")):
                result.append(
                    {
                        "type": kind,
                        "start": match.start("value"),
                        "end": match.end("value"),
                        "score": None,
                        "source": "regex",
                    }
                )
    result.extend(predict_address_chains(text))
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
