"""Версия эталона по калькулятору разметки; исходный вход API не меняется."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from annotation_calculator import Calculator, ROOT
from annotation_contract import assess

VERSION = 'calculator-reviewed-weight-300-v2'
OCCUPATIONS = dict(zip(
    ['редактором', 'инженером', 'бухгалтером', 'дизайнером', 'корректором', 'лаборантом'],
    ['редактор', 'инженер', 'бухгалтер', 'дизайнер', 'корректор', 'лаборант']))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ReviewedCalculator(Calculator):
    """Только проверенные признаки синтетического сценария, без новых формул."""
    def person(self, facts):
        reviewed = deepcopy(facts)
        for fact in reviewed:
            if fact['type'] == 'occupation':
                fact['value'] = OCCUPATIONS.get(fact['value'].casefold(), fact['value'])
            elif fact['type'] == 'address' and 'кв.' in fact['value']:
                # Во всех адресных сценариях этого генератора явно задана
                # личная квартира. Это подтверждение автора, не геокодер.
                fact.update(housing_type='apartment', resolved_level='apartment',
                            reference_region='Российская Федерация')
        return super().person(reviewed)


def inference_content(source):
    return [{k: row[k] for k in ('session_id',)} | {
        'messages': row['gold']['messages'], 'entities': row['gold']['entities'],
        'gold_service_contacts': row.get('gold_service_contacts', [])}
        for row in source['records']]


def validate_overlay(original, corrected):
    if inference_content(original) != inference_content(corrected):
        raise ValueError('Scoring labels changed messages, spans, owners or service contacts')
    if corrected.get('original_content_sha256') != digest(original):
        raise ValueError('Scoring labels belong to a different input snapshot')


def relabel(source, calculator):
    if source.get('version') != 'attribute-combinations-300-v1' or not source.get('synthetic_only'):
        raise ValueError('Author-reviewed enrichment is limited to attribute-combinations-300-v1')
    result = deepcopy(source)
    changes = uncertain = positives = 0
    reasons = Counter()
    for row in result['records']:
        doc = row['gold']
        review = assess(doc, calculator)
        old = row.get('expected_first_alert')
        first = review['first_alert']
        new = first['message_index'] if first else None
        row['previous_author_labels'] = {k: row.get(k) for k in (
            'expected_first_alert', 'author_alert_rationale', 'author_prefix_ledger', 'alert_label_status')}
        row['expected_first_alert'] = new
        row['alert_label_status'] = VERSION
        row['author_alert_rationale'] = first['reason'] if first else 'no_sufficient_accepted_basis'
        row['gold']['case_flags'] = review['case_flags']
        row['calculator_assessment'] = review
        ledger = []
        for index, step in enumerate(review['messages'], 1):
            prefix = deepcopy(doc)
            prefix['messages'] = doc['messages'][:index]
            ids = {m['id'] for m in prefix['messages']}
            prefix['entities'] = [e for e in doc['entities'] if e['message_id'] in ids]
            evaluated = assess(prefix, calculator)
            ledger.append({**step, 'message_index': index, 'people': evaluated['people']})
        row['calculator_prefix_ledger'] = ledger
        # Do not leave the provisional ledger presented as current truth.
        row['author_prefix_ledger'] = review['messages']
        changes += old != new
        uncertain += review['case_flags']['annotation_uncertain']
        positives += new is not None
        reasons[first['reason'] if first else 'no_sufficient_accepted_basis'] += 1
    result.update(original_content_sha256=digest(source), label_policy_version=VERSION,
        positive_sessions=positives,
        annotation_source='authored_spans_and_owners_with_existing_calculator_alert_review',
        limitations=['Synthetic template-based corpus; real-world generalization unmeasured.',
                     'Missing/ambiguous statistics preserved as uncertainty, not proof of safety.',
                     'Working annotation policy; independent human review pending.'],
        label_policy_status='existing_working_annotation_policy_calculator_review',
        evaluation_note_ru='Эталон пересчитан существующим калькулятором разметки, без весов сервиса. '
        'Неуверенные шаги остаются отрицательными по рабочему MVP; это не доказательство безопасности. '
        'Города вымышленные: численность им не назначается. Тип квартиры подтверждён автором сценария.',
        label_review=dict(sessions=len(result['records']), changed_first_alert=changes,
            positive=positives, negative=len(result['records'])-positives,
            sessions_with_uncertain_prefix=uncertain, reasons=dict(reasons),
            policy_version=calculator.policy['version'], policy_sha256=digest(calculator.policy),
            geography_sha256=digest(calculator.geo), housing_sha256=digest(calculator.housing),
            occupation_reference_sha256=digest(calculator.occupations.reference),
            occupation_mapping_sha256=digest(calculator.occupations.mapping),
            sessions_uncertain_at_end=sum(r['calculator_assessment']['messages'][-1]['annotation_uncertain']
                                         for r in result['records']),
            occupation_normalization=OCCUPATIONS,
            assumptions=['Явные личные адреса с кв. подтверждены как квартиры автором синтетического сценария.',
                         'Неоднозначные профессии не получают произвольно выбранного ОКЗ.',
                         'Нет перемножения независимых долей; используются формулы существующего калькулятора.']))
    validate_overlay(source, result)
    return result


def scoring_source(run, original):
    pointer = run / 'scoring-labels.json'
    if not pointer.exists():
        return original, 'summary.json'
    config = json.loads(pointer.read_text(encoding='utf-8'))
    path = ROOT / config['snapshot']
    corrected = json.loads(path.read_text(encoding='utf-8'))
    if digest(corrected) != config['content_sha256']:
        raise ValueError('Scoring snapshot checksum mismatch')
    validate_overlay(original, corrected)
    return corrected, 'summary-corrected.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if not all(p.is_relative_to(ROOT / 'data') for p in (run, output)):
        parser.error('Paths must be inside data/')
    original = json.loads((run / 'results/input-snapshot.json').read_text(encoding='utf-8'))
    corrected = relabel(original, ReviewedCalculator())
    if output.exists():
        if digest(json.loads(output.read_text(encoding='utf-8'))) != digest(corrected):
            parser.error('Existing corrected snapshot differs; use a new version path')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(corrected, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    pointer = dict(snapshot=output.relative_to(ROOT).as_posix(), content_sha256=digest(corrected))
    (run / 'scoring-labels.json').write_text(json.dumps(pointer, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(corrected['label_review'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
