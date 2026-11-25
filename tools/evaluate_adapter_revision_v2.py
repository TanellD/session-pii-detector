"""Сравнение v1, защиты контактов и v2 на тех же записях реального NER."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from evaluate_random_linker import read, write, record_detector, extraction
from evaluate_adapter_revision import StaticDetector
from session_pii import adapter_revision as v1, adapter_revision_v2 as v2
from session_pii.components import EmptyDetector, StubLinker
from session_pii.detectors import DetectorSettings
from session_pii.models import Message, Verdict
from session_pii.pipeline import Pipeline

DATA = ROOT / 'data/experiments'
CORPORA = {
    'synthetic-first': (DATA / 'random-linker-250-2026-11-24/snapshot.json',
                        DATA / 'random-linker-250-gliner-2026-11-24'),
    'synthetic-next': (DATA / 'adapter-next-250-2026-11-25/snapshot.json',
                       DATA / 'adapter-next-250-2026-11-25/ner'),
    'andrey': (DATA / 'andrey-adapter-250-2026-11-25/snapshot.json',
               DATA / 'andrey-adapter-250-2026-11-25/ner'),
}
VARIANTS = {'v1': v1.revise, 'contacts_only': lambda text, result: v2.revise(text, result, names=False),
            'v2': v2.revise}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(corpus, output):
    snapshot, recordings = CORPORA[corpus]
    source = read(snapshot)
    docs = [r['gold'] for r in source['records']]
    if source.get('synthetic_only') is not True and any(
            not d['session_id'].startswith('synthetic-') for d in docs):
        raise ValueError('Нужны явно синтетические данные')
    code = {p.relative_to(ROOT).as_posix(): digest(p) for p in [
        ROOT / 'src/session_pii/adapter_revision.py', ROOT / 'src/session_pii/adapter_revision_v2.py',
        ROOT / 'src/session_pii/pipeline.py', ROOT / 'src/session_pii/normalization.py', Path(__file__)]}
    identity = dict(corpus=corpus, version=v2.VERSION, code_sha256=code,
                    source_sha256=digest(snapshot), recording_sha256=digest(recordings / 'detector-recordings.json'),
                    recording_identity=read(recordings / 'detector-recordings-identity.json'),
                    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'manifest.json'
    if manifest.exists() and read(manifest) != identity:
        raise ValueError('Для изменённой версии нужен новый каталог результатов')
    write(manifest, identity)
    base, _ = record_detector(docs, DetectorSettings(mode='gliner', model_dir=ROOT / 'outputs/gliner-prepared'),
                              recordings, reuse=True)
    cache, trace = {}, []
    raw, runs = {n: [] for n in VARIANTS}, {n: [] for n in VARIANTS}
    latency = {n: [] for n in VARIANTS}
    for doc in docs:
        outputs = {n: [] for n in VARIANTS}
        for i, message in enumerate(doc['messages'], 1):
            text = message['text']
            if text not in cache:
                original = base.detect(Message(message_id=i, text=text))
                cache[text] = {}
                for name, function in VARIANTS.items():
                    start = time.perf_counter()
                    result, rejected = function(text, original)
                    latency[name].append(1000 * (time.perf_counter() - start))
                    cache[text][name] = result
                    trace.append(dict(text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                      variant=name, rejected=rejected))
            for name in VARIANTS:
                outputs[name].append(cache[text][name])
        for name in VARIANTS:
            raw[name].append([dict(message_id=m['id'], type=e.type, start=e.start, end=e.end)
                              for m, result in zip(doc['messages'], outputs[name]) for e in result.spans])
            entities, facts, verdict, error = [], (), Verdict(), None
            for i, (message, result) in enumerate(zip(doc['messages'], outputs[name]), 1):
                try:
                    step = Pipeline(StaticDetector(result), EmptyDetector(), StubLinker()).process(
                        Message(message_id=i, text=message['text']), facts, verdict, session_id=doc['session_id'])
                    facts, verdict = step.facts, step.verdict
                    entities.extend(dict(message_id=message['id'], type=e.type, start=e.start, end=e.end)
                                    for e in step.entities)
                except ValueError as exc:
                    error = dict(message_index=i, reason=str(exc))
                    break
            runs[name].append(dict(session_id=doc['session_id'], entities=entities, error=error))
    summary = dict(corpus=corpus, total_sessions=len(docs), version=v2.VERSION,
                   scope='exact type-aware extraction; frozen real NER; no LLM or alert scoring; diagnostic reused data',
                   raw={n: extraction(docs, predictions) for n, predictions in raw.items()}, filtered={},
                   revision_latency_ms={n: dict(unique_texts=len(values), median=statistics.median(values),
                                               max=max(values)) for n, values in latency.items()})
    for name, rows in runs.items():
        chosen = [i for i, r in enumerate(rows) if r['error'] is None]
        summary['filtered'][name] = dict(completed_sessions=len(chosen),
            errors=[dict(session_id=r['session_id'], **r['error']) for r in rows if r['error']],
            extraction=extraction([docs[i] for i in chosen], [rows[i]['entities'] for i in chosen]))
    common = [i for i in range(len(docs)) if all(runs[n][i]['error'] is None for n in VARIANTS)]
    summary['common_filtered'] = dict(sessions=len(common), **{n: extraction(
        [docs[i] for i in common], [runs[n][i]['entities'] for i in common]) for n in VARIANTS})
    assert all(digest(ROOT / name) == value for name, value in code.items()), 'Код изменился во время оценки'
    write(output / 'summary.json', summary)
    write(output / 'raw-predictions.json', raw)
    write(output / 'filtered-predictions.json', runs)
    write(output / 'rejected-candidates.json', trace)
    write(output / 'environment.json', dict(python=platform.python_version(), platform=platform.platform()))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', choices=[*CORPORA, 'all'], default='all')
    parser.add_argument('--output', type=Path, default=DATA / 'adapter-v2-2026-11-25-r2')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'data'):
        parser.error('Результаты должны храниться в data/')
    names = list(CORPORA) if args.corpus == 'all' else [args.corpus]
    for name in names:
        summary = evaluate(name, args.output.resolve() / name)
        print(json.dumps(dict(corpus=name, raw={n: r['micro'] for n, r in summary['raw'].items()},
            completed={n: r['completed_sessions'] for n, r in summary['filtered'].items()}), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
