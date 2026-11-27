"""Проверка фактически выгруженных обучающих строк против замороженного gold."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_lines(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def owners(entity):
    value = entity['person_id']
    return value if isinstance(value, list) else [] if value is None else [value]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    if not dataset.is_relative_to(ROOT/'data'):
        parser.error('Dataset must remain under data/')
    records = read(dataset/'snapshot.json')['records']
    results = read(dataset/'calculator-assessments.json')
    counts = {}
    for split in ('train', 'validation', 'test'):
        selected = [r for r in records if r['split'] == split]
        ner = load_lines(dataset/split/'ner.jsonl')
        linker = load_lines(dataset/split/'linker-supervised.jsonl')
        alerts = load_lines(dataset/split/'alerts.jsonl')
        index = 0
        for row, alert in zip(selected, alerts):
            doc = row['gold']
            assert alert == dict(session_id=row['session_id'], first_alert=results[row['session_id']]['first_alert'],
                                 prefixes=results[row['session_id']]['messages'])
            past, known, unknown = [], {}, []
            for number, message in enumerate(doc['messages'], 1):
                mentions = [e for e in doc['entities'] if e['message_id'] == message['id']]
                expected = dict(session_id=doc['session_id'], message_id=message['id'], text=message['text'],
                                spans=[{k: e[k] for k in ('start', 'end', 'type')} for e in mentions])
                assert ner[index] == expected, ('NER export differs', doc['session_id'], number)
                item = linker[index]
                assert item['session_id'] == doc['session_id'] and item['message_id'] == message['id']
                request, assignments = item['request'], item['target']['assignments']
                assert set(request) == {'message', 'previous_messages', 'candidates', 'known_people', 'unowned_facts'}
                assert request['message'] == dict(message_id=number, text=message['text'])
                assert request['previous_messages'] == past, 'Future or altered message in supervised input'
                assert {p['person_id']: p['facts'] for p in request['known_people']} == known
                assert request['unowned_facts'] == unknown
                assert len(request['candidates']) == len(mentions) == len(assignments)
                new = {}
                for i, (entity, candidate, assignment) in enumerate(zip(mentions, request['candidates'], assignments), 1):
                    value = message['text'][entity['start']:entity['end']]
                    assert candidate == dict(candidate_id=f'c{i}', type=entity['type'], start=entity['start'], end=entity['end'], value=value)
                    assert assignment['candidate_id'] == candidate['candidate_id']
                    assert assignment['service_contact'] == (entity['id'] in row['gold_service_contacts'])
                    actual = assignment['owners']
                    expected_owners = owners(entity)
                    assert len(actual) == len(expected_owners) == len(set(actual))
                    for prediction, person in zip(actual, expected_owners):
                        if person in known:
                            assert prediction == person
                        else:
                            assert prediction.startswith('new:')
                            assert new.setdefault(prediction, person) == person
                    assert len(new.values()) == len(set(new.values())), 'One new owner assigned multiple tokens'
                for entity in mentions:
                    fact = dict(type=entity['type'], value=message['text'][entity['start']:entity['end']])
                    if not owners(entity) and entity['id'] not in row['gold_service_contacts']:
                        unknown.append(fact)
                    for person in owners(entity):
                        known.setdefault(person, []).append(fact)
                past.append(dict(message_id=number, text=message['text']))
                index += 1
        assert index == len(ner) == len(linker)
        assert len(alerts) == len(selected)
        counts[split] = dict(ner_rows=len(ner), linker_rows=len(linker), alert_rows=len(alerts))
    report = dict(status='passed', checks=['ner_spans_equal_gold', 'all_messages_including_empty',
                 'linker_prefix_only_no_future_gold', 'new_shared_unknown_service_targets', 'all_alert_exports_equal_calculator'], counts=counts)
    (dataset/'export-validation.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
