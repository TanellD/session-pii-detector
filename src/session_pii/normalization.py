"""Разрешение согласованных пересечений до линковки и расчёта риска."""
from .models import SuppressedSpan

NAME_PARTS = {'first_name', 'last_name', 'patronymic'}
DOCUMENTS = {'passport', 'snils', 'individual_inn', 'bank_card'}


def overlaps(a, b):
    return max(a.start, b.start) < min(a.end, b.end)


def contains(a, b):
    return a.start <= b.start and b.end <= a.end


def winner(a, b):
    for outer, inner in ((a, b), (b, a)):
        if outer.type in DOCUMENTS and inner.type == 'phone':
            return outer, inner, 'document_over_phone'
        if contains(outer, inner):
            if outer.type == 'organization' and inner.type in NAME_PARTS | {'person'}:
                return outer, inner, 'name_inside_organization'
            if outer.type == 'address' and inner.type == 'geography':
                return outer, inner, 'geography_inside_address'
            if outer.type in {'email', 'phone'} and inner.type == 'person':
                return outer, inner, 'person_inside_contact'
            if outer.type == 'person' and inner.type in NAME_PARTS:
                return inner, outer, 'typed_name_over_person'
    return None


def resolve(candidates):
    suppressed = []
    # Более специфические спаны имени обрабатываются до общего PERSON.
    ordered = sorted(candidates, key=lambda s: (s.type == 'person', s.start, s.end, s.type))
    kept = []
    for candidate in ordered:
        conflicts = [other for other in kept if overlaps(candidate, other)]
        decisions = [winner(candidate, other) for other in conflicts]
        if any(decision is None for decision in decisions):
            # Не выбираем тип по несопоставимым confidence. Состояние не изменится.
            raise ValueError('Unresolved cross-type overlap')
        lost = next((d for d in decisions if d[1] is candidate), None)
        if lost:
            suppressed.append(SuppressedSpan(type=candidate.type, start=candidate.start,
                end=candidate.end, reason=lost[2], kept_type=lost[0].type))
            continue
        for decision in decisions:
            other = decision[1]
            kept.remove(other)
            suppressed.append(SuppressedSpan(type=other.type, start=other.start, end=other.end,
                reason=decision[2], kept_type=candidate.type))
        kept.append(candidate)
    return tuple(sorted(kept, key=lambda s: (s.start, s.end, s.type))), tuple(suppressed)


def fact_value(kind, value):
    if kind in {'phone', 'passport', 'snils', 'individual_inn', 'bank_card'}:
        digits = ''.join(c for c in value if c.isdecimal())
        if kind == 'phone' and len(digits) == 11 and digits.startswith('8'):
            digits = '7' + digits[1:]
        return digits
    return ' '.join(value.split()).casefold()
