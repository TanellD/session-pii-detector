"""Локальное разделение замороженной разметки по числу людей и длине."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def group_key(doc):
    owners = set()
    for entity in doc['entities']:
        value = entity['person_id']
        if value is not None:
            owners.update(value if isinstance(value, list) else [value])
    count, length = len(owners), len(doc['messages'])
    bucket = 'short' if 2 <= length <= 5 else 'long' if length >= 6 else 'outside'
    return count, bucket


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must remain in data/')
    if args.output.exists():
        parser.error('Use a new directory')
    raw = args.input.read_bytes()
    source = json.loads(raw)
    if source.get('synthetic_only') is not True:
        parser.error('Only explicitly synthetic inputs')
    records = source['records']
    ids = [r['session_id'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate session IDs')
    cells = {(n, length): [] for n in range(4) for length in ('short', 'long')}
    outside, mapping, exceptions = [], [], []
    for record in records:
        count, bucket = group_key(record['gold'])
        target = f'people-{count}/{bucket}'
        if (count, bucket) in cells:
            cells[count, bucket].append(record)
        else:
            outside.append(record)
            target = 'outside'
        positive = record['expected_first_alert'] is not None
        if count == 0 and positive:
            exceptions.append(record['session_id'])
        mapping.append(dict(session_id=record['session_id'], people_count=count,
                            user_messages=len(record['gold']['messages']), group=target,
                            gold_positive=positive, final_uncertain=record['final_uncertain']))
    args.output.mkdir(parents=True)

    def write(path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    summary = []
    for (count, bucket), rows in cells.items():
        directory = args.output/f'people-{count}'/bucket
        write(directory/'snapshot.json', dict(synthetic_only=True, records=rows))
        inputs = [dict(session_id=r['session_id'], messages=r['gold']['messages']) for r in rows]
        write(directory/'sessions.json', inputs)
        write(directory/'gold.json', [r['gold'] for r in rows])
        summary.append(dict(people_count=count, length=bucket, sessions=len(rows),
                            positive=sum(r['expected_first_alert'] is not None for r in rows),
                            negative_certain=sum(r['expected_first_alert'] is None and not r['final_uncertain'] for r in rows),
                            negative_uncertain=sum(r['expected_first_alert'] is None and r['final_uncertain'] for r in rows)))
    write(args.output/'outside.json', outside)
    write(args.output/'mapping.json', mapping)
    report = dict(grouping_policy='people-length-v1', generation_policy='source_preserved',
                  source=str(args.input), source_sha256=hashlib.sha256(raw).hexdigest(),
                  sessions=len(records), groups=summary, outside=len(outside),
                  zero_people_positive_exceptions=exceptions, benchmark_ready=False,
                  notes='Gold and original generation metadata preserved; no new model inference or train/test split.')
    write(args.output/'manifest.json', report)
    exported = [r for rows in cells.values() for r in rows] + outside
    assert Counter(r['session_id'] for r in exported) == Counter(ids)
    assert {r['session_id']: r for r in exported} == {r['session_id']: r for r in records}
    assert args.input.read_bytes() == raw
    lines = ['# Группы по числу людей и длине', '',
             'Локальное представление исходной партии. Тексты и эталон не изменены. '
             'Число людей — разные ненулевые владельцы в разметке. '
             'Это не train/test и не результат работы сервиса.', '',
             '| Людей | Длина | Сессий | Алерт нужен | Нет алерта | Нет алерта, недостаток статистики |',
             '|---:|---|---:|---:|---:|---:|']
    for row in summary:
        folder = f'people-{row["people_count"]}/{row["length"]}'
        label = '2–5 сообщений' if row['length'] == 'short' else 'от 6 сообщений'
        lines.append(f'| {row["people_count"]} | [{label}]({folder}/sessions.json) | {row["sessions"]} | '
                     f'{row["positive"]} | {row["negative_certain"]} | {row["negative_uncertain"]} |')
    lines.extend(['', 'Состав сессий: [mapping.json](mapping.json). В каждой папке есть sessions.json, gold.json и snapshot.json.',
                  'Исходные easy/medium/hard сохранены только как исторические метаданные. '
                  'Субъективная оценка сложности Gemma не определяет новые группы.',
                  'У нулевых отрицательных контролей после запуска возможны FP/TN; до запуска таких исходов ещё нет. '
                  'Положительные исключения с нулём владельцев сохраняются в manifest.json.',
                  'Пустые ячейки не заполнены искусственно. Готовность полного benchmark не заявляется.', ''])
    (args.output/'overview.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
