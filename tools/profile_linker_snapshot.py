"""Структура фиксированного синтетического среза для интерпретации метрик линкера."""
from collections import Counter
from itertools import combinations
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / 'data/experiments/random-linker-250-2026-11-24/snapshot.json'
    rows = json.loads(source.read_text(encoding='utf-8'))['records']
    counts, lengths = Counter(), Counter()
    def owners(entity):
        value = entity['person_id']
        return set(value if isinstance(value, list) else [] if value is None else [value])
    for row in rows:
        doc = row['gold']
        entities = doc['entities']
        people = {owner for entity in entities for owner in owners(entity)}
        counts['empty_entities'] += not entities
        counts['one_known_person'] += len(people) == 1
        counts['two_plus_people'] += len(people) >= 2
        counts['unknown_only'] += bool(entities) and not people
        counts['shared_sessions'] += any(len(owners(entity)) > 1 for entity in entities)
        counts['fewer_than_two_known_mentions'] += sum(bool(owners(entity)) for entity in entities) < 2
        lengths[len(doc['messages'])] += 1
        for first, second in combinations(entities, 2):
            if owners(first) & owners(second):
                counts['gold_positive_pairs'] += 1
    print(json.dumps(dict(total=len(rows), counts=dict(counts), message_counts=dict(lengths),
                         families=len({row['family'] for row in rows})), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
