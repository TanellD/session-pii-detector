"""Пакет синтетических данных и локального runtime для сравнения линкеров."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_runtime(output):
    archive = output.with_suffix('.zip')
    runtime_files = []
    for directory in ['src', 'tools', 'configs', 'benchmarks']:
        runtime_files.extend(p for p in (ROOT/directory).rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py', '.json', '.txt'})
    runtime_files.extend([ROOT/'docs/kb/metrics.md', ROOT/'docs/data/cheap-linker-benchmark.md'])
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in output.rglob('*'):
            if path.is_file():
                z.write(path, path.relative_to(ROOT).as_posix())
        for path in runtime_files:
            z.write(path, path.relative_to(ROOT).as_posix())
        z.writestr('runtime-manifest.json', json.dumps({p.relative_to(ROOT).as_posix(): sha(p)
                                                      for p in runtime_files}, indent=2)+'\n')
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--ner', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refresh-archive', action='store_true', help='Verify existing dataset and rebuild ZIP with current runtime')
    args = parser.parse_args()
    dataset, ner, output = args.dataset.resolve(), args.ner.resolve(), args.output.resolve()
    if not output.is_relative_to(ROOT/'data'):
        parser.error('Output must be under data/')
    if args.refresh_archive:
        for name, digest in read(output/'manifest.json')['files'].items():
            if sha(output/name) != digest:
                raise ValueError('Existing package checksum mismatch: '+name)
        archive = archive_runtime(output)
        print(json.dumps(dict(archive=str(archive), archive_sha256=sha(archive))))
        return
    if output.exists():
        parser.error('Use a new output directory under data/')
    manifest = read(dataset/'manifest.json')
    if not manifest['synthetic_only'] or read(ner/'status.json')['stage'] != 'complete':
        raise ValueError('Require synthetic data and a complete NER export')
    export = read(ner/'manifest.json')
    counts = {}
    for split in ['train', 'validation', 'test']:
        source = read(dataset/split/'sessions.json')
        predictions_path = ner/split/'predictions.jsonl'
        if sha(predictions_path) != export['exports'][split]['sha256']:
            raise ValueError('NER export checksum mismatch')
        if sha(dataset/split/'sessions.json') != export['identity']['inputs'][split]:
            raise ValueError('NER export belongs to a different input')
        predictions = [json.loads(line) for line in predictions_path.read_text(encoding='utf-8').splitlines()]
        if len(predictions) != len(source):
            raise ValueError('Session count mismatch')
        by_id = {p['session_id']: p for p in predictions}
        for row in source:
            frozen = by_id[row['session_id']]
            if [(m['id'], m['text']) for m in row['messages']] != [(m['message_id'], m['text']) for m in frozen['messages']]:
                raise ValueError('Text or message ID mismatch')
            for message in frozen['messages']:
                for span in message['spans']:
                    if message['text'][span['start']:span['end']] != span['value'] or 'person_id' in span:
                        raise ValueError('Invalid or labelled inference input')
        files = {'inputs/sessions.json': dataset/split/'sessions.json',
                 'inputs/ner.jsonl': predictions_path, 'targets/snapshot.json': dataset/split/'snapshot.json',
                 'targets/linker-supervised.jsonl': dataset/split/'linker-supervised.jsonl'}
        for name, source_path in files.items():
            target = output/split/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        counts[split] = dict(sessions=len(source), messages=sum(len(r['messages']) for r in source))
    shutil.copyfile(ROOT/'configs/runtime-risk-policy.json', output/'baseline-policy.json')
    shutil.copyfile(ROOT/'docs/data/cheap-linker-benchmark.md', output/'README.md')
    shutil.copyfile(dataset/'overview.md', output/'dataset-card.md')
    (output/'requirements.txt').write_text('pydantic>=2.10,<3\nnumpy>=1.26,<3\n', encoding='utf-8')
    files = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob('*') if p.is_file()}
    result = dict(version='cheap-linker-benchmark-v1', synthetic_only=True,
                  dataset_version=manifest['version'], splits=counts,
                  source_annotation=manifest['annotation_source'], independent_human_review=False,
                  files=files, ner_identity=export['identity'],
                  policy='Fit on validation only; test is frozen; report failed sessions separately.',
                  builder_sha256=sha(Path(__file__)))
    (output/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    archive = archive_runtime(output)
    print(json.dumps(dict(output=str(output), archive=str(archive), splits=counts,
                          archive_sha256=sha(archive)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
