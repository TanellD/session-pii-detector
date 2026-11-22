"""Adapter v2 rules implementation."""

import datetime
import re

try:
    from russiannames.parser import NamesParser
except ImportError:
    NamesParser = None

from benchmarks.pii_smoke.regex_detector import valid_inn, valid_snils, valid_luhn
from benchmarks.pii_smoke.phone_format import valid_phone_span

RULES_VERSION = "pii_smoke_rules-v3"

OCCUPATION_WORDS = {
    "директор",
    "менеджер",
    "курьер",
    "инженер",
    "бухгалтер",
    "врач",
    "учитель",
    "продавец",
    "водитель",
    "секретарь",
    "начальник",
    "заместитель",
    "руководитель",
    "специалист",
    "консультант",
    "администратор",
    "оператор",
    "мастер",
    "г-н",
    "господин",
    "госпожа",
    "гражданин",
    "гражданка",
}

RELATIONSHIP_WORDS = {
    "дочка",
    "дочь",
    "сын",
    "сынок",
    "мама",
    "мать",
    "папа",
    "отец",
    "жена",
    "муж",
    "брат",
    "сестра",
    "бабушка",
    "дедушка",
    "тётя",
    "тетя",
    "дядя",
    "племянник",
    "племянница",
    "родственник",
    "родственница",
    "родитель",
    "родительница",
}

PLACE_WORDS = {
    "офис",
    "склад",
    "магазин",
    "точка",
    "филиал",
    "представительство",
    "отдел",
    "управление",
    "департамент",
    "служба",
    "сектор",
}

SERVICE_LOCAL_PARTS = {
    "info",
    "support",
    "sales",
    "admin",
    "help",
    "service",
    "noreply",
    "no-reply",
    "office",
    "contact",
    "mail",
    "team",
    "billing",
    "hr",
    "press",
    "feedback",
}
SERVICE_DOMAINS = {
    "company-mail.ru",
    "shop-express.ru",
    "corp.ru",
    "example.invalid",
}


def overlap(a, b):
    return max(a["start"], b["start"]) < min(a["end"], b["end"])


def classify_email(text, entity):
    value = text[entity["start"] : entity["end"]].lower()
    local, _, domain = value.partition("@")
    if local in SERVICE_LOCAL_PARTS or domain in SERVICE_DOMAINS:
        return "SERVICE_EMAIL"
    return "EMAIL"


def reclassify_emails(message, entities):
    result = []
    for entity in entities:
        if entity["type"] == "EMAIL":
            result.append(
                {
                    **entity,
                    "type": classify_email(message, entity),
                    "detector_version": RULES_VERSION,
                }
            )
        else:
            result.append(entity)
    return result


DOB_RE = re.compile(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*")


def age_from_dob(value):
    match = DOB_RE.fullmatch(value.strip())
    if not match:
        return None
    day, month, year = (int(group) for group in match.groups())
    try:
        born = datetime.date(year, month, day)
    except ValueError:
        return None
    today = datetime.date.today()
    if born > today:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def enrich_dob(message, entities):
    result = []
    for entity in entities:
        if entity["type"] == "DATE_OF_BIRTH":
            attrs = dict(entity.get("attributes") or {})
            age = age_from_dob(message[entity["start"] : entity["end"]])
            if age is not None:
                attrs["age"] = age
                result.append({**entity, "attributes": attrs})
                continue
        result.append(entity)
    return result


ADDRESS_PART_TYPES = {"ADDRESS", "CITY"}
ADDRESS_GAP_RE = re.compile(r"[\s,]+")
ADDRESS_WEAK_RE = re.compile(r"\d{1,6}(?:[А-Яа-я](?:[/-]\d+)?)?")

ADDRESS_KEYWORDS = {
    "ул",
    "улица",
    "пр",
    "проспект",
    "пер",
    "переулок",
    "б-р",
    "бульвар",
    "г",
    "город",
    "д",
    "дом",
    "кв",
    "квартира",
    "корп",
    "корпус",
    "стр",
    "строение",
    "ш",
    "шоссе",
    "пл",
    "площадь",
    "наб",
    "набережная",
    "индекс",
    "почтовый",
}


def has_address_keyword(text):
    lower = text.lower()
    return any(kw in lower.split() for kw in ADDRESS_KEYWORDS)


def merge_addresses(message, entities):
    result = [entity for entity in entities if entity["type"] not in ADDRESS_PART_TYPES]
    parts = sorted(
        (entity for entity in entities if entity["type"] in ADDRESS_PART_TYPES),
        key=lambda entity: (entity["start"], entity["end"]),
    )
    filtered = []
    for part in parts:
        text = message[part["start"] : part["end"]]
        if part["type"] == "ADDRESS" and not has_address_keyword(text):
            window_start = max(0, part["start"] - 50)
            window_end = min(len(message), part["end"] + 50)
            window = message[window_start:window_end]
            if not has_address_keyword(window):
                continue
        filtered.append(part)
    parts = filtered
    clusters = []
    for part in parts:
        if clusters:
            previous = clusters[-1][-1]
            gap = message[previous["end"] : part["start"]]
            if ADDRESS_GAP_RE.fullmatch(gap):
                clusters[-1].append(part)
                continue
        clusters.append([part])

    for cluster in clusters:
        if len(cluster) == 1:
            single = cluster[0]
            text = message[single["start"] : single["end"]]
            if single["type"] == "CITY":
                result.append(single)
            elif ADDRESS_WEAK_RE.fullmatch(text):
                continue
            else:
                result.append(single)
            continue
        start = cluster[0]["start"]
        end = max(part["end"] for part in cluster)
        sources = {part["source"] for part in cluster}
        result.append(
            {
                "type": "ADDRESS",
                "start": start,
                "end": end,
                "score": next(
                    (p.get("score") for p in cluster if p.get("score") is not None),
                    None,
                ),
                "source": "regex" if sources == {"regex"} else "gliner",
                "detector_version": RULES_VERSION,
            }
        )
    return result

RU_PHONE_RE = re.compile(
    r"^(?:\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}$"
)

PRONOUNS = {
    "я",
    "ты",
    "он",
    "она",
    "оно",
    "мы",
    "вы",
    "они",
    "меня",
    "тебя",
    "его",
    "её",
    "нас",
    "вас",
    "их",
    "мне",
    "тебе",
    "ему",
    "ей",
    "нам",
    "вам",
    "им",
    "мой",
    "моя",
    "моё",
    "мои",
    "твой",
    "твоя",
    "твоё",
    "твои",
    "наш",
    "наша",
    "наше",
    "наши",
    "ваш",
    "ваша",
    "ваше",
    "ваши",
}

_KIND_BY_CHAR = {"s": "LAST_NAME", "f": "FIRST_NAME", "m": "PATRONYMIC"}
_KEY_BY_CHAR = {"s": "sn", "f": "fn", "m": "mn"}
_PARSER = None


def get_names_parser():
    global _PARSER
    if _PARSER is None:
        if NamesParser is None:
            raise RuntimeError("russiannames is not installed")
        _PARSER = NamesParser()
    return _PARSER


def _find_value(core, offset, value):
    for candidate in (value + ".", value):
        position = core.find(candidate)
        if position >= 0:
            return offset + position, offset + position + len(candidate)
    return None


PATRONYMIC_SUFFIX_RE = re.compile(r"(овна|евна|ична|инична|ович|евич|ич)$", re.I)

INITIALS_FULL = re.compile(
    r"^(?P<surname>[А-ЯЁ][а-яё-]+)\s+(?P<first>[А-ЯЁ])\.\s*(?P<patronymic>[А-ЯЁ])\.$"
)
INITIALS_REVERSED = re.compile(
    r"^(?P<first>[А-ЯЁ])\.\s*(?P<patronymic>[А-ЯЁ])\.\s*(?P<surname>[А-ЯЁ][а-яё-]+)$"
)
INITIALS_ONE_SURNAME = re.compile(
    r"^(?P<first>[А-ЯЁ])\.\s*(?P<surname>[А-ЯЁ][а-яё-]+)$"
)
INITIALS_SURNAME_ONE = re.compile(
    r"^(?P<surname>[А-ЯЁ][а-яё-]+)\s+(?P<first>[А-ЯЁ])\.$"
)


def _initials_parts(core, entity, message):
    """Return list of Mention-like dicts for initials forms, or None."""
    match = INITIALS_FULL.match(core)
    if match:
        groups = match.groups()
        kinds = ("LAST_NAME", "FIRST_NAME", "PATRONYMIC")
    else:
        match = INITIALS_REVERSED.match(core)
        if match:
            groups = match.groups()
            kinds = ("FIRST_NAME", "PATRONYMIC", "LAST_NAME")
        else:
            match = INITIALS_ONE_SURNAME.match(core)
            if match:
                groups = match.groups()
                kinds = ("FIRST_NAME", "LAST_NAME")
            else:
                match = INITIALS_SURNAME_ONE.match(core)
                if match:
                    groups = match.groups()
                    kinds = ("LAST_NAME", "FIRST_NAME")
                else:
                    return None
    parts = []
    position = entity["start"]
    for kind, value in zip(kinds, groups):
        found = message.find(value, position)
        if found < 0:
            return None
        parts.append(
            {
                **entity,
                "type": kind,
                "start": found,
                "end": found + len(value),
                "text": value,
            }
        )
        position = found + len(value)
    return parts


def _two_token_with_role(message, entity, tokens):
    """Fix for 2-token spans like 'Менеджер Волкова', 'Г-н Смирнов'.

    Returns list of parts if the first token is an occupation/title and
    the second token looks like a last name, otherwise None.
    """
    first, second = tokens
    if first.lower() not in OCCUPATION_WORDS:
        return None
    position = entity["start"]
    first_start = message.find(first, position)
    if first_start < 0:
        return None
    second_start = message.find(second, first_start + len(first))
    if second_start < 0:
        return None
    return [
        {
            **entity,
            "type": "OCCUPATION",
            "start": first_start,
            "end": first_start + len(first),
            "text": first,
        },
        {
            **entity,
            "type": "LAST_NAME",
            "start": second_start,
            "end": second_start + len(second),
            "text": second,
        },
    ]


NAME_TOKEN = r"[А-ЯЁа-яё]+(?:[-‐‑][А-ЯЁа-яё]+)*"
NAME_CONTEXT = (("FIRST_NAME", "имя"), ("LAST_NAME", "фамилия|фамилии"),
                ("PATRONYMIC", "отчество|отчества"))

def extract_name_context(message):
    result = []
    for kind, cue in NAME_CONTEXT:
        pattern = rf"\b(?:{cue})\s*(?:[:—–-]\s*)?(?P<value>{NAME_TOKEN})"
        for match in re.finditer(pattern, message, re.I):
            value = match.group("value")
            if value.casefold() in PRONOUNS or len(value) < 2:
                continue
            result.append(dict(type=kind, start=match.start("value"), end=match.end("value"),
                               source="regex", score=None, detector_version=RULES_VERSION))
    return result


def split_person(message, entity):
    start, end = entity["start"], entity["end"]
    fragment = message[start:end]
    core = fragment.strip()
    frag_offset = start + len(fragment) - len(fragment.lstrip())

    if any(len(token.rstrip('.')) < 2 or '.' in token for token in core.split()):
        return []  # Initials are outside the agreed MVP annotation scope.

    tokens = fragment.split()

    if len(tokens) == 1 and PATRONYMIC_SUFFIX_RE.search(tokens[0]):
        token = tokens[0]
        position = message.find(token, start)
        return [
            {
                **entity,
                "type": "PATRONYMIC",
                "start": position,
                "end": position + len(token),
                "text": token,
            }
        ]

    if len(tokens) == 2:
        two = _two_token_with_role(message, entity, tokens)
        if two:
            return two

    parsed = None
    if NamesParser is not None:
        try:
            parsed = get_names_parser().parse(core)
        except Exception:
            parsed = None

    if parsed and parsed.get("parsed") and parsed.get("format"):
        parts = []
        for char in parsed["format"]:
            key = _KEY_BY_CHAR.get(char.lower())
            if key is None:
                continue
            value = parsed.get(key + "_s") if char.isupper() else parsed.get(key)
            if not value:
                continue
            found = _find_value(core, frag_offset, value)
            if found is None:
                position = message.find(value, frag_offset, end)
                if position < 0:
                    break
                found = (position, position + len(value))
            parts.append(
                {
                    **entity,
                    "type": _KIND_BY_CHAR[char.lower()],
                    "start": found[0],
                    "end": found[1],
                    "text": value,
                }
            )
        expected = sum(char.lower() in _KIND_BY_CHAR for char in parsed["format"])
        if parts and len(parts) == expected:
            return sorted(parts, key=lambda part: (part["start"], part["end"]))

    if len(tokens) == 3:
        position = start
        parts = []
        for kind, token in zip(("FIRST_NAME", "PATRONYMIC", "LAST_NAME"), tokens):
            position = message.find(token, position)
            if position < 0:
                break
            parts.append(
                {
                    **entity,
                    "type": kind,
                    "start": position,
                    "end": position + len(token),
                    "text": token,
                }
            )
            position += len(token)
        if len(parts) == 3:
            return parts
    if len(tokens) == 2 and "зовут" in message[:start]:
        position = start
        parts = []
        for kind, token in (("FIRST_NAME", tokens[0]), ("LAST_NAME", tokens[1])):
            position = message.find(token, position)
            parts.append(
                {
                    **entity,
                    "type": kind,
                    "start": position,
                    "end": position + len(token),
                    "text": token,
                }
            )
            position += len(token)
        return parts
    if len(tokens) == 1:
        if "зовут" in message[:start].lower():
            return [{**entity, "type": "FIRST_NAME"}]
        return [{**entity, "type": "PERSON"}]
    return [{**entity, "type": "PERSON"}]


def merge_organizations(message, entities):
    result = list(entities)
    for match in re.finditer(
        r"(?i)(?:ООО|АО|ИП|ПАО|АНО)\s*[«\"]([^»\"]+)[»\"]", message
    ):
        span = {
            "type": "ORGANIZATION",
            "start": match.start(),
            "end": match.end(),
            "score": None,
            "source": "regex",
            "detector_version": RULES_VERSION,
        }
        result = [
            entity
            for entity in result
            if not (overlap(entity, span) and entity["type"] == "ORGANIZATION")
        ]
        result.append(span)
    return result


def resolve_conflicts(message, entities):
    result = []
    suppressed_phones = []
    for inn in (entity for entity in entities if entity["type"] == "INN"):
        value = message[inn["start"] : inn["end"]]
        if len("".join(char for char in value if char.isdigit())) == 12 and valid_inn(
            value
        ):
            suppressed_phones.extend(
                phone
                for phone in entities
                if phone["type"] == "PHONE" and overlap(inn, phone)
            )

    for entity in entities:
        if entity["type"] in ["LAST_NAME", "FIRST_NAME", "PATRONYMIC", "PERSON"]:
            value = message[entity["start"] : entity["end"]].strip().lower()
            if value in PRONOUNS:
                continue
        if entity["type"] in {"INN", "SNILS", "CARD"}:
            check = {"INN": valid_inn, "SNILS": valid_snils, "CARD": valid_luhn}[entity["type"]]
            if not check(message[entity["start"]:entity["end"]]):
                continue
        if entity["type"] == "ORGANIZATION":
            value = message[entity["start"] : entity["end"]].strip().lower()
            words = value.split()
            if value in PLACE_WORDS or (words and words[0] in PLACE_WORDS):
                continue
        if entity["type"] == "PHONE":
            value = message[entity["start"] : entity["end"]].strip()
            if not valid_phone_span(message, entity["start"], entity["end"]):
                continue
            if any(overlap(entity, phone) for phone in suppressed_phones):
                continue

        overlaps = [e for e in result if e["type"] == entity["type"] and overlap(e, entity)]
        if overlaps and any(e["end"] - e["start"] >= entity["end"] - entity["start"] for e in overlaps):
            continue
        result = [e for e in result if e not in overlaps]
        result.append(entity)

    return sorted(
        result, key=lambda entity: (entity["start"], entity["end"], entity["type"])
    )


def validate_spans(message, entities):
    for entity in entities:
        if not 0 <= entity["start"] < entity["end"] <= len(message):
            raise ValueError(
                f"Invalid span {entity['type']} [{entity['start']}:{entity['end']}]"
            )


def apply_rules(message, entities, detector_version=None):
    validate_spans(message, entities)
    result = []
    for entity in entities:
        if entity["type"] in {"DATE_OF_BIRTH", "RELATIONSHIP", "SPECIAL_CATEGORY"}:
            continue
        if entity["type"] == "PERSON":
            value = message[entity["start"] : entity["end"]].strip().lower()
            if value in OCCUPATION_WORDS:
                result.append(
                    {**entity, "type": "OCCUPATION", "detector_version": RULES_VERSION}
                )
                continue
            result.extend(split_person(message, entity))
        else:
            result.append(entity)
    # Explicit contextual types take precedence only on their own spans.
    context = extract_name_context(message)
    result = [e for e in result if not any(overlap(e, c) and e["type"] in
              {"PERSON", "FIRST_NAME", "LAST_NAME", "PATRONYMIC"} for c in context)]
    result.extend(context)
    result = merge_addresses(message, result)
    result = merge_organizations(message, result)
    return resolve_conflicts(message, result)
