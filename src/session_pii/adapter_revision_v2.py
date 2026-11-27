"""Новая ревизия обработки контактов и явных конструкций представления.

Не изменяет замороженную v1 и не получает эталон, владельцев или историю.
"""
import hashlib
import re

from . import adapter_revision as v1
from .components import DetectionResult
from .models import MentionOrigin, Span

VERSION = 'adapter-candidate-v2'
NAME_TYPES = {'person', 'first_name', 'last_name', 'patronymic'}
CONTACT_INNER = NAME_TYPES | {'organization', 'age', 'geography', 'occupation', 'sex'}
INTRO = re.compile(
    r'(?i:\bя|\bменя\s+зовут|\bзаявитель\s*:|\bконтактное\s+лицо\s*:)'
    r'\s+(?P<value>' + v1.CAP + r'(?:[ \t]+' + v1.CAP + r'){1,2})(?![а-яёА-ЯЁ-])')
RELATIVE = re.compile(
    r'(?i:\bмой\s+(?:сосед|коллега|брат|друг)|\bмоя\s+(?:соседка|коллега|сестра|подруга))'
    r'[ \t]*[—–:-][ \t]*(?P<value>' + v1.CAP + r')(?![а-яёА-ЯЁ-])')
ALIAS = re.compile(
    r'(?i:\bпсевдоним(?:ом)?|\bник(?:нейм)?)[ \t]*[:—-]?[ \t]*'
    r'«(?P<value>[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z_-]{1,39})»')


def rule_span(kind, start, end):
    ident = hashlib.sha256(f'{VERSION}:{kind}:{start}:{end}'.encode()).hexdigest()[:20]
    return Span(type=kind, start=start, end=end, provenance=(MentionOrigin(
        mention_id=ident, source='regex', detector_version=VERSION),))


def contact_filter(text, detection):
    """Валидный контакт не содержит независимых семантических атрибутов.

    Документы внутри PHONE здесь не подавляются: их приоритет задан сервисом.
    Частичные пересечения также оставляем сервису как явный конфликт.
    """
    contacts = [e for e in detection.spans if e.type in {'email', 'phone'}]
    kept, rejected = [], []
    for span in detection.spans:
        outer = next((c for c in contacts if c.start <= span.start and span.end <= c.end
                      and span.type in CONTACT_INNER), None)
        if outer is None:
            kept.append(span)
        else:
            rejected.append(dict(type=span.type, start=span.start, end=span.end,
                                 reason='semantic_attribute_inside_contact', kept_type=outer.type))
    return DetectionResult(tuple(kept), detection.complete, detection.reasons), rejected


def name_roles(tokens):
    """Только сочетания с признаками фамилии/отчества; без догадок по одному слову."""
    values = [t.group() for t in tokens]
    if len(values) == 3:
        if v1.SURNAME.search(values[0]) and v1.PATRONYMIC.search(values[2]):
            return ['last_name', 'first_name', 'patronymic']
        if v1.PATRONYMIC.search(values[1]) and v1.SURNAME.search(values[2]):
            return ['first_name', 'patronymic', 'last_name']
    if len(values) == 2:
        if v1.SURNAME.search(values[0]) and not v1.SURNAME.search(values[1]):
            return ['last_name', 'first_name']
        if not v1.SURNAME.search(values[0]) and v1.SURNAME.search(values[1]):
            return ['first_name', 'last_name']
        if v1.PATRONYMIC.search(values[1]):
            return ['first_name', 'patronymic']
    return None


def revise(text, detection, *, names=True):
    original, rejected = v1.revise(text, detection)
    original, nested = contact_filter(text, original)
    rejected.extend(nested)
    if not names:
        return original, rejected
    replacements = []
    for match in INTRO.finditer(text):
        tokens = list(v1.WORD.finditer(match['value']))
        roles = name_roles(tokens)
        if roles:
            replacements.append((match.start('value'), match.end('value'), [
                rule_span(kind, match.start('value') + token.start(), match.start('value') + token.end())
                for kind, token in zip(roles, tokens)]))
    for pattern, kind in [(RELATIVE, 'first_name'), (ALIAS, 'person')]:
        for match in pattern.finditer(text):
            replacements.append((*match.span('value'), [rule_span(kind, *match.span('value'))]))
    candidates = list(original.spans)
    for start, end, parts in replacements:
        # Явное название организации или контакт важнее правила имени.
        if any(e.type in {'organization', 'address', 'email', 'phone'} and
               max(e.start, start) < min(e.end, end) for e in candidates):
            continue
        if any(e.type in NAME_TYPES and max(e.start, start) < min(e.end, end)
               and not (start <= e.start and e.end <= end) for e in candidates):
            continue
        kept = []
        for span in candidates:
            if start <= span.start and span.end <= end and span.type in NAME_TYPES:
                rejected.append(dict(type=span.type, start=span.start, end=span.end,
                                     reason='explicit_name_context_retyping'))
            else:
                kept.append(span)
        candidates = kept + parts
    unique = {(e.start, e.end, e.type): e for e in candidates}
    return DetectionResult(tuple(sorted(unique.values(), key=lambda e: (e.start, e.end, e.type))),
                           original.complete, original.reasons), rejected


class RevisedAdapter:
    def __init__(self, base):
        self.base = base

    def detect(self, message):
        return revise(message.text, self.base.detect(message))[0]
