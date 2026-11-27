"""Сохранить только regex + GLiNER v2, без владельцев, риска и сравнения с gold."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from session_pii.adapter_revision_v2 import RevisedAdapter
from session_pii.components import DetectionResult
from session_pii.detectors import AdapterDetector, DetectorSettings
from session_pii.models import Message, Span


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CachedBase:
    def __init__(self, directory):
        self.directory = directory

    def detect(self, message):
        value = read(self.directory/(hashlib.sha256(message.text.encode()).hexdigest()+'.json'))
        return DetectionResult(tuple(Span.model_validate_json(json.dumps(s)) for s in value['spans']),
                               value['complete'], tuple(value['reasons']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse-run', type=Path, required=True)
    parser.add_argument('--splits', nargs='+', choices=['train', 'validation', 'test'], default=['train', 'validation', 'test'])
    args = parser.parse_args()
    dataset, output, reuse = args.dataset.resolve(), args.output.resolve(), args.reuse_run.resolve()
    if not output.is_relative_to(ROOT/'data') or output == dataset or dataset.is_relative_to(output):
        parser.error('Output must be a separate directory under data/')
    if not read(dataset/'manifest.json').get('synthetic_only'):
        parser.error('Only synthetic data supported')
    files = ['benchmarks/pii_smoke/config.json', 'src/session_pii/detectors.py',
             'src/session_pii/adapter_revision.py', 'src/session_pii/adapter_revision_v2.py',
             'benchmarks/adapter/adapter_gliner.py', 'benchmarks/adapter/adapter_contract.py', 'benchmarks/adapter/_rules_impl.py']
    identity = dict(dataset=dataset.name, inputs={s: sha(dataset/s/'sessions.json') for s in ['train', 'validation', 'test']},
                    files={name: sha(ROOT/name) for name in files}, stage='RevisedAdapter v2 before service filtering')
    settings = DetectorSettings(mode='gliner', model_dir=ROOT/'outputs/gliner-prepared')
    identity['model_manifest'] = sha(settings.model_dir/'manifest.json') if (settings.model_dir/'manifest.json').exists() else None
    if (output/'identity.json').exists() and read(output/'identity.json') != identity:
        raise ValueError('Export identity changed; use a separate output')
    # The reusable base cache must match the detector dependencies frozen by validation.
    guard = read(reuse/'dependency-guard.json')
    for name, value in guard.items():
        if (ROOT/name).is_file() and sha(ROOT/name) != value:
            raise ValueError('Reusable cache dependency changed: '+name)
    saved_identity = read(reuse/'identity.json')
    # Compare detector identity through the same function used by the original run.
    from tools.evaluate_random_linker import recording_identity
    frozen_docs = [r['gold'] for r in read(reuse/'input-snapshot.json')['records']]
    if saved_identity['detector'] != recording_identity(frozen_docs, settings):
        raise ValueError('Reusable detector identity mismatch')
    write(output/'identity.json', identity)
    directory = output/'base-by-text'
    directory.mkdir(parents=True, exist_ok=True)
    for path in (reuse/'ner/by-text').glob('*.json'):
        if not (directory/path.name).exists():
            shutil.copyfile(path, directory/path.name)
    sessions = {split: read(dataset/split/'sessions.json') for split in args.splits}
    texts = list(dict.fromkeys(m['text'] for rows in sessions.values() for row in rows for m in row['messages']))
    missing = [t for t in texts if not (directory/(hashlib.sha256(t.encode()).hexdigest()+'.json')).exists()]
    def status(stage, done, **extra):
        state = dict(updated_at=datetime.now(timezone.utc).isoformat(), stage=stage,
                     texts_total=len(texts), texts_completed=done, splits=args.splits, **extra)
        write(output/'status.json', state)
        (output/'live-progress.md').write_text('# Только regex + GLiNER v2\n\n'+
            f"Обновлено UTC: {state['updated_at']}\n\nЭтап: {stage}. Тексты: {done}/{len(texts)}.\n\n"+
            'Линкер, риск и метрики относительно gold не запускаются. Результаты: split/predictions.jsonl; восстановление по кэшу.\n', encoding='utf-8')
    status('ner', len(texts)-len(missing))
    if missing:
        import torch
        torch.set_num_threads(read(settings.config_path)['threads'])
        detector = AdapterDetector(settings)
        for index, text in enumerate(missing, 1):
            start = time.monotonic()
            result = detector.detect(Message(message_id=1, text=text))
            write(directory/(hashlib.sha256(text.encode()).hexdigest()+'.json'),
                  dict(spans=[s.model_dump(mode='json') for s in result.spans], complete=result.complete,
                       reasons=result.reasons, seconds=time.monotonic()-start))
            if index % 10 == 0 or index == len(missing):
                status('ner', len(texts)-len(missing)+index)
                print(f'NER {len(texts)-len(missing)+index}/{len(texts)}', flush=True)
    detector = RevisedAdapter(CachedBase(directory))
    status('export', len(texts))
    counts = {}
    for split, rows in sessions.items():
        target = output/split/'predictions.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.jsonl.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            for row in rows:
                messages = []
                for index, message in enumerate(row['messages'], 1):
                    result = detector.detect(Message(message_id=index, text=message['text']))
                    spans = [{**s.model_dump(mode='json'), 'value': message['text'][s.start:s.end]} for s in result.spans]
                    messages.append(dict(message_id=message['id'], text=message['text'], spans=spans,
                                         complete=result.complete, reasons=result.reasons))
                stream.write(json.dumps(dict(session_id=row['session_id'], messages=messages), ensure_ascii=False)+'\n')
        temporary.replace(target)
        counts[split] = dict(sessions=len(rows), messages=sum(len(r['messages']) for r in rows), sha256=sha(target))
    write(output/'manifest.json', dict(identity=identity, exports=counts, owners_included=False,
                                      risk_included=False, gold_used_for_predictions=False,
                                      code_sha256=sha(Path(__file__))))
    status('complete', len(texts), exports=counts)


if __name__ == '__main__':
    main()
