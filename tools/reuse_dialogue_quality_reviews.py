"""Повторное ревью только изменённых текстов с проверяемым происхождением."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def messages_hash(row):
    return hashlib.sha256(json.dumps(row['gold']['messages'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--previous-dataset', type=Path)
    parser.add_argument('--previous-review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Output must remain in data/')
    current = read(args.dataset/'review-sample.json')
    rows = {r['session_id']:r for r in current['records']}
    digest = hashlib.sha256((args.dataset/'review-sample.json').read_bytes()).hexdigest()
    if not args.finalize:
        if args.output.exists():
            parser.error('Use a new output directory')
        previous = {r['session_id']:r for r in read(args.previous_dataset/'review-sample.json')['records']}
        old = read(args.previous_review/'summary.json')
        assert old['completed'] <= old['total']
        old_manifest = read(args.previous_review/'manifest.json')
        assert old_manifest['input_sha256'] == hashlib.sha256((args.previous_dataset/'review-sample.json').read_bytes()).hexdigest()
        reviewed = {r['session_id']:r for r in old['reviews']}
        inherited, pending = [], []
        for sid, row in rows.items():
            if sid in previous and sid in reviewed and messages_hash(row) == messages_hash(previous[sid]):
                inherited.append(dict(**reviewed[sid], origin=str(args.previous_review), messages_sha256=messages_hash(row)))
            else:
                pending.append(row)
        args.output.mkdir(parents=True)
        write(args.output/'provenance.json', dict(final_sample_sha256=digest, previous_review=str(args.previous_review),
                                                 inherited=len(inherited), to_review=len(pending), total=len(rows)))
        write(args.output/'inherited-reviews.json', inherited)
        write(args.output/'delta-snapshot.json', dict(synthetic_only=True, version=current['version'], records=pending))
        print(json.dumps(read(args.output/'provenance.json'), indent=2))
        return
    provenance = read(args.output/'provenance.json')
    assert provenance['final_sample_sha256'] == digest
    inherited = read(args.output/'inherited-reviews.json')
    for row in inherited:
        assert row['messages_sha256'] == messages_hash(rows[row['session_id']])
    new = read(args.output/'delta-run/summary.json')
    identity = read(args.output/'delta-run/manifest.json')
    assert identity['input_sha256'] == hashlib.sha256((args.output/'delta-snapshot.json').read_bytes()).hexdigest()
    combined = list(inherited)
    for row in new['reviews']:
        combined.append(dict(**row, origin=str(args.output/'delta-run'), messages_sha256=messages_hash(rows[row['session_id']])))
    assert len({r['session_id'] for r in combined}) == len(combined)
    assert all(r['session_id'] in rows for r in combined)
    report = dict(completed=len(combined), total=len(rows), inherited=len(inherited),
                  reviewed_again=len(new['reviews']), verdicts=dict(Counter(r['verdict'] for r in combined)),
                  issue_categories=dict(Counter(i['category'] for r in combined for i in r['issues'])),
                  reviews=combined, final_sample_sha256=digest)
    write(args.output/'summary.json', report)
    print(json.dumps({k:v for k,v in report.items() if k != 'reviews'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
