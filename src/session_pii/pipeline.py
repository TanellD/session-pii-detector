"""Новое сообщение → NER/regexp → проверка/дедупликация → линкер → правила."""
from collections import defaultdict
from dataclasses import dataclass

from tools.annotation_contract import validate
from tools.ownership import owner_ids

from .components import Detector, Fact, Linker
from .models import Candidate, Entity, Message, Span, Verdict, MentionKey, SuppressedSpan
from .normalization import resolve
from .risk import RiskEvaluator


@dataclass(frozen=True)
class Step:
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    verdict: Verdict
    suppressed: tuple[SuppressedSpan, ...] = ()


class Pipeline:
    def __init__(self, ner: Detector, regexp: Detector, linker: Linker, evaluator: RiskEvaluator | None = None):
        self.ner, self.regexp, self.linker = ner, regexp, linker
        # Без скрытых скачиваний, внешних API и файлов data/.
        self.evaluator = evaluator or RiskEvaluator()
        self.policy_version = self.evaluator.policy.version

    def process(self, message: Message, facts: tuple[Fact, ...], previous: Verdict, *,
                session_id: str = "runtime", session_epoch: str = "runtime") -> Step:
        candidates = {}
        incomplete = list(previous.incomplete_reasons)
        for name, detector in (('ner', self.ner), ('regexp', self.regexp)):
            output = detector.detect(message)
            if not output.complete:
                incomplete.extend(output.reasons or (f'{name}_incomplete',))
            for span in output.spans:
                if not isinstance(span, Span):
                    raise ValueError('Detector must return Span objects')
                self._validate(message, (span,), (None,))
                sources = tuple(dict.fromkeys(o.source for o in span.provenance)) or (name,)
                key = (span.start, span.end, span.type)
                if key in candidates:
                    old = candidates[key]
                    # Оценки разных детекторов не сравниваем и не усредняем.
                    candidates[key] = old.model_copy(update={'score': None, 'sources': tuple(dict.fromkeys(old.sources+sources)),
                        'provenance': tuple(dict.fromkeys(old.provenance+span.provenance))})
                else:
                    candidates[key] = Candidate(**span.model_dump(), sources=sources)
        ordered, suppressed = resolve(tuple(candidates[k] for k in sorted(candidates)))
        self._validate(message, ordered, tuple(None for _ in ordered))
        linked = self.linker.link(message, ordered, facts)
        if len(linked.person_ids) != len(ordered):
            raise ValueError('Linker must preserve entity count and order')
        if not linked.complete:
            incomplete.extend(linked.reasons or ('linker_incomplete',))
        self._validate(message, ordered, linked.person_ids)
        service_contacts = linked.service_contacts or tuple(False for _ in ordered)
        if len(service_contacts) != len(ordered) or any(
                not isinstance(flag, bool) or (flag and (s.type not in ('phone', 'email') or p is not None))
                for s,p,flag in zip(ordered,linked.person_ids,service_contacts)):
            raise ValueError('Service contacts must be unassigned phone/email entities')
        entities = tuple(Entity(**span.model_dump(), id=f'm{message.message_id}-e{n}',
                                person_id=tuple(person) if isinstance(person, list) else person, service_contact=flag,
                                mention_keys=tuple(MentionKey(session_id=session_id, session_epoch=session_epoch,
                                  message_id=message.message_id, mention_id=o.mention_id) for o in span.provenance))
                         for n,(span,person,flag) in enumerate(zip(ordered,linked.person_ids,service_contacts),1))
        new = tuple(Fact(e.type, message.text[e.start:e.end], e.person_id, e.id, message.message_id, e.service_contact, e.provenance, e.mention_keys)
                    for e in entities)
        all_facts = facts+new
        people = defaultdict(list)
        unknown = []
        for fact in all_facts:
            if fact.person_id is None:
                unknown.append(fact)
                continue
            for person in owner_ids(fact.person_id):
                people[person].append(fact)
        assessments = {p:self.evaluator.assess(tuple(rows)) for p,rows in people.items()}
        unknown_risk = self.evaluator.assess(tuple(unknown), unknown=True)
        all_assessments = (*assessments.values(), unknown_risk)
        reason = next((rule for rule in ('red_rule', 'combination_rule', 'score_rule')
                       if any(rule in a.rules for a in all_assessments)), None)
        if any(a.missing_weights for a in all_assessments):
            incomplete.append('risk_weights_not_configured')
        first = previous.first_alert_message_id
        verdict = Verdict(session_alert=bool(first is not None or reason),current_alert=bool(reason),
                          first_alert_message_id=first if first is not None else message.message_id if reason else None,
                          first_alert_reason=previous.first_alert_reason if first is not None else reason,
                          annotation_uncertain=False,
                          annotation_uncertain_ever=previous.annotation_uncertain_ever,
                          coverage_complete=not incomplete,incomplete_reasons=tuple(dict.fromkeys(incomplete)),
                          person_risks=assessments, unknown_risk=unknown_risk)
        return Step(entities,all_facts,verdict,suppressed)

    @staticmethod
    def _validate(message, spans, people):
        validate(dict(schema_version='annotation-mvp-v2',session_id='runtime',
                      messages=[dict(id=str(message.message_id),text=message.text)],
                      entities=[dict(id=str(n),message_id=str(message.message_id),start=s.start,end=s.end,
                                     type=s.type,person_id=list(p) if isinstance(p, tuple) else p) for n,(s,p) in enumerate(zip(spans,people))],
                      case_flags=dict(is_example=False,annotation_uncertain=False,clarification_reason=None)))
