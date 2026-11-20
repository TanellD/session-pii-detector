"""Новое сообщение → NER/regexp → проверка/дедупликация → линкер → правила."""
from collections import defaultdict
from dataclasses import dataclass

from tools.annotation_calculator import Calculator, RED
from tools.annotation_contract import validate

from .components import Detector, Fact, Linker
from .models import Candidate, Entity, Message, Span, Verdict


@dataclass(frozen=True)
class Step:
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    verdict: Verdict


class Pipeline:
    def __init__(self, ner: Detector, regexp: Detector, linker: Linker):
        self.ner, self.regexp, self.linker = ner, regexp, linker
        # Без скрытых скачиваний, внешних API и файлов data/.
        self.calculator = Calculator(policy_only=True)
        self.policy_version = self.calculator.policy['version']

    def process(self, message: Message, facts: tuple[Fact, ...], previous: Verdict) -> Step:
        candidates = {}
        incomplete = list(previous.incomplete_reasons)
        for name, detector in (('ner', self.ner), ('regexp', self.regexp)):
            output = detector.detect(message)
            if not output.complete:
                incomplete.extend(output.reasons or (f'{name}_incomplete',))
            for span in output.spans:
                if not isinstance(span, Span):
                    raise ValueError('Detector must return Span objects')
                key = (span.start, span.end, span.type)
                if key in candidates:
                    old = candidates[key]
                    # Оценки разных детекторов не сравниваем и не усредняем.
                    candidates[key] = old.model_copy(update={'score': None, 'sources': tuple(dict.fromkeys(old.sources+(name,)))})
                else:
                    candidates[key] = Candidate(**span.model_dump(), sources=(name,))
        ordered = tuple(candidates[k] for k in sorted(candidates))
        self._validate(message, ordered, tuple(None for _ in ordered))
        linked = self.linker.link(message, ordered, facts)
        if len(linked.person_ids) != len(ordered):
            raise ValueError('Linker must preserve entity count and order')
        if not linked.complete:
            incomplete.extend(linked.reasons or ('linker_incomplete',))
        self._validate(message, ordered, linked.person_ids)
        entities = tuple(Entity(**span.model_dump(), id=f'm{message.message_id}-e{n}', person_id=person)
                         for n,(span,person) in enumerate(zip(ordered,linked.person_ids),1))
        new = tuple(Fact(e.type, message.text[e.start:e.end], e.person_id, e.id, message.message_id)
                    for e in entities)
        all_facts = facts+new
        people = defaultdict(list)
        unknown_red = False
        for fact in all_facts:
            if fact.person_id is None:
                unknown_red |= fact.type in RED
                continue
            value = fact.value
            if fact.type=='sex':
                value = {'мужской':'male','мужчина':'male','женский':'female','женщина':'female'}.get(value.casefold(),value)
            row = dict(type=fact.type,value=value,person_id=fact.person_id,complete=True,
                       entity_id=fact.entity_id,message_id=str(fact.message_id))
            if fact.type in ('phone','email'):
                row['usage'] = 'personal'
            if fact.type=='address':
                row['completeness'] = 'full'
            if fact.type=='organization':
                row['exact'] = True
            people[fact.person_id].append(row)
        assessments = {p:self.calculator.person(rows) for p,rows in people.items()}
        reason = 'red_rule' if unknown_red else None
        for rule in ('red_rule','combination_rule','candidate_count_rule'):
            if reason is None and any(a['alert_reason']==rule for a in assessments.values()):
                reason = rule
        uncertain = not reason and any(a['alert'] is None for a in assessments.values())
        first = previous.first_alert_message_id
        verdict = Verdict(session_alert=bool(first is not None or reason),current_alert=bool(reason),
                          first_alert_message_id=first if first is not None else message.message_id if reason else None,
                          first_alert_reason=previous.first_alert_reason if first is not None else reason,
                          annotation_uncertain=uncertain,
                          annotation_uncertain_ever=previous.annotation_uncertain_ever or uncertain,
                          clarification_reason='insufficient_statistics' if uncertain else None,
                          coverage_complete=not incomplete,incomplete_reasons=tuple(dict.fromkeys(incomplete)),
                          candidate_counts={p:a['candidate_count'] for p,a in assessments.items()})
        return Step(entities,all_facts,verdict)

    @staticmethod
    def _validate(message, spans, people):
        validate(dict(schema_version='annotation-mvp-v2',session_id='runtime',
                      messages=[dict(id=str(message.message_id),text=message.text)],
                      entities=[dict(id=str(n),message_id=str(message.message_id),start=s.start,end=s.end,
                                     type=s.type,person_id=p) for n,(s,p) in enumerate(zip(spans,people))],
                      case_flags=dict(is_example=False,annotation_uncertain=False,clarification_reason=None)))
