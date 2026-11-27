"""Покрытие соседства текстов между частями без значений сущностей."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from build_session_benchmark import read, write


def words(row):
    doc, pieces = row['gold'], []
    for message in doc['messages']:
        text = message['text']
        spans = [dict(e) for e in doc['entities'] if e['message_id'] == message['id']]
        spans += [dict(c, type=c['kind']) for c in row['negative_controls'] if c['message_id'] == message['id']]
        for e in sorted(spans, key=lambda e:e['start'], reverse=True):
            text = text[:e['start']]+' ENTITY_'+e['type']+' '+text[e['end']:]
        pieces += re.findall(r'\w+', text.casefold())
    # Bigrams preserve some order; unigrams avoid a score dominated by exact
    # syntactic paraphrases. Scores describe overlap, not identity of intent.
    return set(pieces) | {' '.join(pieces[i:i+2]) for i in range(len(pieces)-1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args()
    output = args.dataset.resolve()
    if not output.is_relative_to(ROOT/'data'):
        parser.error('Dataset must be under data/')
    source = read(output/'snapshot.json')
    rows = source['records']
    vectors = {r['session_id']: words(r) for r in rows}
    train = [r for r in rows if r['split'] == 'train']
    nearest = []
    for row in rows:
        if row['split'] == 'train':
            continue
        left = vectors[row['session_id']]
        best_score, best = -1, None
        for other in train:
            right = vectors[other['session_id']]
            score = len(left & right)/len(left | right)
            if score > best_score:
                best_score, best = score, other
        nearest.append(dict(session_id=row['session_id'], split=row['split'], nearest_train_id=best['session_id'],
                            similarity=round(best_score, 6), family=row['family'], nearest_family=best['family']))
    result = dict(source_sha256=hashlib.sha256((output/'snapshot.json').read_bytes()).hexdigest(),
                  metric='Jaccard of word unigrams+bigrams after replacing all annotated and negative-control spans by types',
                  interpretation='Descriptive overlap; not semantic independence or a model metric.',
                  splits={split: dict(sessions=sum(r['split'] == split for r in nearest),
                                     max=max(r['similarity'] for r in nearest if r['split'] == split),
                                     median=statistics.median(r['similarity'] for r in nearest if r['split'] == split),
                                     at_least_080=sum(r['split'] == split and r['similarity'] >= .8 for r in nearest))
                          for split in ('validation', 'test')},
                  top30=sorted(nearest, key=lambda r:r['similarity'], reverse=True)[:30])
    write(output/'text-overlap-audit.json', result)
    print(json.dumps({k:v for k,v in result.items() if k != 'top30'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
