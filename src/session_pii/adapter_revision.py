"""Отдельная экспериментальная ревизия поверх неизменённых выходов адаптера."""
import hashlib
import re

from benchmarks.pii_smoke.regex_detector import predict_address_chains, valid_inn
from .components import DetectionResult
from .models import MentionOrigin, Span, Message
from .pipeline import Pipeline

VERSION = 'adapter-candidate-v1'
CAP = r'[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)*'
WORD = re.compile(CAP)
PATRONYMIC = re.compile(r'(?:ович|евич|ич|овна|евна|ична)$', re.I)
SURNAME = re.compile(r'(?:ов|ев|ёв|ин|ын|ова|ева|ёва|ина|ына|ский|ская|цкий|цкая|енко|ко)$', re.I)
NON_PERSON = {'мы оба','другой человек','другие люди','заявитель','сосед','соседка',
              'коллега','человек','мужчина','женщина','имя','фамилия','отчество',
              'я','ты','он','она','мы','вы','они','мой','моя','мои','его','её','ее','наш','наша'}
PASSPORT = re.compile(r'(?i:\bпаспорт(?:а)?\b)[ \t]*(?:[:№][ \t]*)?'
    r'(?:(?i:серия[ \t]+и[ \t]+номер|серия|номер)[ \t]*[:№]?[ \t]*)?'
    r'(?P<value>[0-9]{2}[ ]?[0-9]{2}[ ][0-9]{6})(?![0-9])')
INN = re.compile(r'(?i:\bИНН\b)[ \t]*(?:(?i:физического[ \t]+лица|физлица)[ \t]*)?'
    r'[:№]?[ \t]*(?P<value>[0-9]{12})(?![0-9])')
CONTEXT = [
    ('last_name', re.compile(r'(?i:\bфамили[яю])\s*[:—-]?\s*(?P<value>'+CAP+r')')),
    ('first_name', re.compile(r'(?i:\bимя|\bзовут)\s*[:—-]?\s*(?P<value>'+CAP+r')')),
    ('patronymic', re.compile(r'(?i:\bотчеств[оа])\s*[:—-]?\s*(?P<value>'+CAP+r')')),
    ('first_name', re.compile(r'(?i:\bколлегу|\bсоседа|\bсоседку)\s*[:—-]?\s*(?P<value>'+CAP+r')')),
]


def rule_span(kind, start, end):
    ident = hashlib.sha256(f'{VERSION}:{kind}:{start}:{end}'.encode()).hexdigest()[:20]
    return Span(type=kind, start=start, end=end, provenance=(MentionOrigin(
        mention_id=ident, source='regex', detector_version=VERSION),))


def person_parts(text, span):
    """Типизация только внутри PERSON; неизвестное остаётся общим PERSON."""
    fragment = text[span.start:span.end]
    tokens = list(WORD.finditer(fragment))
    if not tokens or not re.fullmatch(r'\s*'+CAP+r'(?:\s+'+CAP+r')*\s*',fragment):
        return [span]
    roles = None
    if len(tokens) == 1:
        value = tokens[0].group()
        if PATRONYMIC.search(value):
            roles = ['patronymic']
        elif SURNAME.search(value):
            roles = ['last_name']
    elif len(tokens) == 2:
        first, second = [t.group() for t in tokens]
        if PATRONYMIC.search(second):
            roles = ['first_name','patronymic']
        elif SURNAME.search(first) and not SURNAME.search(second):
            roles = ['last_name','first_name']
        else:
            roles = ['first_name','last_name']
    elif len(tokens) == 3:
        values = [t.group() for t in tokens]
        if PATRONYMIC.search(values[2]):
            roles = ['last_name','first_name','patronymic']
        elif PATRONYMIC.search(values[1]):
            roles = ['first_name','patronymic','last_name']
    if roles is None:
        return [span]
    return [rule_span(kind,span.start+t.start(),span.start+t.end()) for kind,t in zip(roles,tokens)]


def revise(text, detection):
    """Не получает эталон, семейство сценария, владельцев или последующие сообщения."""
    additions = [rule_span('address',e['start'],e['end']) for e in predict_address_chains(text)]
    for pattern, kind in [(PASSPORT,'passport'),(INN,'individual_inn')]:
        for match in pattern.finditer(text):
            if kind != 'individual_inn' or valid_inn(match['value']):
                additions.append(rule_span(kind,*match.span('value')))
    for kind, pattern in CONTEXT:
        additions.extend(rule_span(kind,*m.span('value')) for m in pattern.finditer(text))
    addresses = [e for e in additions if e.type == 'address']
    candidates, rejected = [], []
    for span in (*detection.spans,*additions):
        value = text[span.start:span.end]
        reason = None
        if span.type == 'person' and value.casefold().strip() in NON_PERSON:
            reason = 'non_person_reference'
        if span.type in {'person','first_name','last_name','patronymic','geography'} and any(
                a.start <= span.start and span.end <= a.end for a in addresses):
            reason = 'inside_full_address'
        if reason:
            rejected.append(dict(type=span.type,start=span.start,end=span.end,reason=reason))
            continue
        rows = person_parts(text,span) if span.type == 'person' else [span]
        for candidate in rows:
            try:
                Pipeline._validate(Message(message_id=1,text=text),(candidate,),(None,))
            except ValueError as exc:
                rejected.append(dict(type=candidate.type,start=candidate.start,end=candidate.end,
                                     reason='format_contract',detail=str(exc)))
                continue
            candidates.append(candidate)
    # Контекст — самостоятельное правило, не перенос границ модельного PERSON.
    context_keys={(e.start,e.end,e.type) for e in additions if e.type in {'first_name','last_name','patronymic'}}
    candidates=[e for e in candidates if not any(e.start==s and e.end==t and e.type in
        {'person','first_name','last_name','patronymic'} and e.type!=kind for s,t,kind in context_keys)]
    unique={(e.start,e.end,e.type):e for e in candidates}
    return DetectionResult(tuple(sorted(unique.values(),key=lambda e:(e.start,e.end,e.type))),
                           detection.complete,detection.reasons), rejected


class RevisedAdapter:
    def __init__(self, base):
        self.base=base

    def detect(self, message):
        return revise(message.text,self.base.detect(message))[0]
