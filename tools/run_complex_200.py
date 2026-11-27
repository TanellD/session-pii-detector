"""Свежий NER, сравнение v1/v2 и полный сервис с Gemma на авторском наборе."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from evaluate_random_linker import read, write, record_detector, extraction
from evaluate_adapter_revision import StaticDetector
from session_pii import adapter_revision as v1, adapter_revision_v2 as v2
from session_pii.detectors import DetectorSettings
from session_pii.components import EmptyDetector, StubLinker
from session_pii.models import Message, Verdict
from session_pii.pipeline import Pipeline


def prepare(snapshot, run):
    source = read(snapshot)
    if source.get('synthetic_only') is not True:
        raise ValueError('Нужен синтетический корпус')
    docs = [r['gold'] for r in source['records']]
    ner = run / 'ner'
    ner.mkdir(parents=True, exist_ok=True)
    settings = DetectorSettings(mode='gliner', model_dir=ROOT / 'outputs/gliner-prepared')
    detector, _ = record_detector(docs, settings, ner, reuse=(ner / 'detector-recordings.json').exists())
    raw = {'v1': [], 'v2': []}
    filtered, errors = {'v1': [], 'v2': []}, {'v1': [], 'v2': []}
    cache = {}
    for doc in docs:
        for name, function in [('v1', v1.revise), ('v2', v2.revise)]:
            predictions, entities, facts, verdict, error = [], [], (), Verdict(), None
            for i, message in enumerate(doc['messages'], 1):
                key = name, message['text']
                if key not in cache:
                    cache[key] = function(message['text'], detector.detect(Message(
                        message_id=i, text=message['text'])))[0]
                result = cache[key]
                predictions.extend(dict(message_id=message['id'], start=e.start, end=e.end, type=e.type)
                                   for e in result.spans)
                if error is not None:
                    continue
                try:
                    step = Pipeline(StaticDetector(result), EmptyDetector(), StubLinker()).process(
                        Message(message_id=i, text=message['text']), facts, verdict, session_id=doc['session_id'])
                    facts, verdict = step.facts, step.verdict
                    entities.extend(dict(message_id=message['id'], start=e.start, end=e.end, type=e.type)
                                    for e in step.entities)
                except ValueError as exc:
                    error = dict(session_id=doc['session_id'], message_index=i, reason=str(exc))
                    errors[name].append(error)
            raw[name].append(predictions)
            filtered[name].append(None if error else entities)
    summary = dict(total_sessions=len(docs), scope='fresh real GLiNER; fixed v1 and v2; no LLM in extraction metrics',
                   input_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                   revisions={name: hashlib.sha256((ROOT / f'src/session_pii/adapter_revision{suffix}.py').read_bytes()).hexdigest()
                              for name, suffix in [('v1', ''), ('v2', '_v2')]},
                   raw={n: extraction(docs, rows) for n, rows in raw.items()}, errors=errors, filtered={})
    for name in filtered:
        indices = [i for i, row in enumerate(filtered[name]) if row is not None]
        summary['filtered'][name] = dict(completed_sessions=len(indices), extraction=extraction(
            [docs[i] for i in indices], [filtered[name][i] for i in indices]))
    common = [i for i in range(len(docs)) if all(filtered[n][i] is not None for n in filtered)]
    summary['common_filtered'] = dict(sessions=len(common), **{n: extraction(
        [docs[i] for i in common], [filtered[n][i] for i in common]) for n in filtered})
    write(run / 'ner-comparison.json', summary)
    write(run / 'ner-predictions.json', dict(raw=raw, filtered=filtered))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--ner-only', action='store_true')
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(ROOT / 'data'):
        parser.error('Результаты должны оставаться в data/')
    run.mkdir(parents=True, exist_ok=True)
    source = read(args.input)
    write(run / 'preparation.json', dict(input_snapshot=str(args.input.resolve()),
        total_sessions=len(source['records']),
        total_messages=sum(len(r['gold']['messages']) for r in source['records']),
        unique_texts=len({m['text'] for r in source['records'] for m in r['gold']['messages']})))
    write(run / 'stage.json', dict(stage='fresh_ner', at=datetime.now(timezone.utc).isoformat()))
    prepare(args.input.resolve(), run)
    write(run / 'stage.json', dict(stage='ner_complete' if args.ner_only else 'gemma_service',
                                  at=datetime.now(timezone.utc).isoformat()))
    if args.ner_only:
        return
    command = [sys.executable, '-X', 'utf8', '-u', str(ROOT / 'tools/supervise_gemma_run.py'),
               '--run', str(run), '--input', str(args.input.resolve()), '--ner-recordings', str(run / 'ner'),
               '--adapter-version', 'v2']
    result = subprocess.run(command, cwd=ROOT)
    write(run / 'stage.json', dict(stage='worker_stopped', exit_code=result.returncode,
                                  at=datetime.now(timezone.utc).isoformat()))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
