"""Сравнить отдельную ревизию адаптера на замороженных реальных выходах GLiNER."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import platform

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools'),str(ROOT)]
from evaluate_random_linker import read, write, record_detector, extraction
from session_pii.adapter_revision import revise, VERSION
from session_pii.components import EmptyDetector, StubLinker, DetectionResult
from session_pii.detectors import DetectorSettings
from session_pii.models import Message, Verdict
from session_pii.pipeline import Pipeline


class StaticDetector:
    def __init__(self,result): self.result=result
    def detect(self,message): return self.result


def evaluate(output,snapshot=None,recording_dir=None,record_fresh=False):
    snapshot=snapshot or ROOT/'data/experiments/random-linker-250-2026-11-24/snapshot.json'
    source=read(snapshot)
    docs=[r['gold'] for r in source['records']]
    revision_path=ROOT/'src/session_pii/adapter_revision.py'
    frozen_revision=hashlib.sha256(revision_path.read_bytes()).hexdigest()
    if source.get('frozen_revision_sha256',frozen_revision)!=frozen_revision:
        raise ValueError('Ревизия отличается от зафиксированной до проверки нового среза')
    if len({d['session_id'] for d in docs})!=len(docs) or (
        source.get('synthetic_only') is not True and
        any(not d['session_id'].startswith('synthetic-') for d in docs)):
        raise ValueError('Нужен синтетический срез с уникальными ID')
    recording_dir=recording_dir or ROOT/'data/experiments/random-linker-250-gliner-2026-11-24'
    if record_fresh:
        recording_dir.mkdir(parents=True,exist_ok=True)
        if (recording_dir/'detector-recordings.json').exists():
            raise ValueError('Для повторного использования предсказаний уберите --record-fresh')
    settings=DetectorSettings(mode='gliner',model_dir=ROOT/'outputs/gliner-prepared')
    base,_=record_detector(docs,settings,recording_dir,reuse=not record_fresh)
    output.mkdir(parents=True,exist_ok=True)
    identity=dict(version=VERSION,input_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        baseline_recordings_sha256=hashlib.sha256((recording_dir/'detector-recordings.json').read_bytes()).hexdigest(),
        baseline_identity=read(recording_dir/'detector-recordings-identity.json'),
        revision_sha256=hashlib.sha256((ROOT/'src/session_pii/adapter_revision.py').read_bytes()).hexdigest())
    manifest=output/'manifest.json'
    if manifest.exists() and read(manifest)!=identity:
        raise ValueError('Для изменённого кода или данных нужен новый каталог')
    write(manifest,identity)
    raw={'baseline':[],'revised':[]}
    runs={'baseline':[],'revised':[]}
    trace=[]
    cache={}
    for doc in docs:
        outputs={'baseline':[],'revised':[]}
        for i,m in enumerate(doc['messages'],1):
            if m['text'] not in cache:
                original=base.detect(Message(message_id=i,text=m['text']))
                start=time.perf_counter()
                changed,rejected=revise(m['text'],original)
                cache[m['text']]=(original,changed,rejected,(time.perf_counter()-start)*1000)
            original,changed,rejected,elapsed=cache[m['text']]
            for name,result in [('baseline',original),('revised',changed)]:
                outputs[name].append(result)
            trace.append(dict(session_id=doc['session_id'],message_id=m['id'],rejected=rejected))
        for name in raw:
            raw[name].append([dict(message_id=m['id'],type=e.type,start=e.start,end=e.end)
                for m,result in zip(doc['messages'],outputs[name]) for e in result.spans])
            entities=[]
            error=None
            # Только экстракция: StubLinker не назначает владельцев, алерты не оцениваем.
            facts,verdict=(),Verdict()
            for i,(m,result) in enumerate(zip(doc['messages'],outputs[name]),1):
                try:
                    step=Pipeline(StaticDetector(result),EmptyDetector(),StubLinker()).process(
                        Message(message_id=i,text=m['text']),facts,verdict,session_id=doc['session_id'])
                    facts,verdict=step.facts,step.verdict
                    entities.extend(dict(message_id=m['id'],type=e.type,start=e.start,end=e.end) for e in step.entities)
                except ValueError as exc:
                    error=dict(message_index=i,reason=str(exc))
                    break
            runs[name].append(dict(session_id=doc['session_id'],entities=entities,error=error))
    summary=dict(version=VERSION,total_sessions=len(docs),
        scope='fixed real GLiNER+regex outputs; weights and threshold unchanged; no LLM; exact type-aware span metrics',
        raw={name:extraction(docs,preds) for name,preds in raw.items()},filtered={},
        revision_latency_ms=dict(unique_texts=len(cache),median=statistics.median(v[3] for v in cache.values()),
            max=max(v[3] for v in cache.values()),scope='revision postprocessing only, no neural inference'),
        tuning_scope=source.get('evaluation_scope','diagnostic synthetic set used to design some rules; not independent holdout'))
    for name,rows in runs.items():
        chosen=[i for i,r in enumerate(rows) if r['error'] is None]
        summary['filtered'][name]=dict(completed_sessions=len(chosen),
            errors=[dict(session_id=r['session_id'],**r['error']) for r in rows if r['error']],
            extraction=extraction([docs[i] for i in chosen],[rows[i]['entities'] for i in chosen]))
    common=[i for i in range(len(docs)) if all(runs[name][i]['error'] is None for name in runs)]
    summary['common_filtered']=dict(sessions=len(common),**{name:extraction(
        [docs[i] for i in common],[rows[i]['entities'] for i in common]) for name,rows in runs.items()})
    if hashlib.sha256(revision_path.read_bytes()).hexdigest()!=frozen_revision:
        raise ValueError('Правила изменились во время проверки')
    write(output/'summary.json',summary)
    write(output/'raw-predictions.json',raw)
    write(output/'filtered-predictions.json',runs)
    write(output/'rejected-candidates.json',trace)
    records=[dict(text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        spans=[e.model_dump(mode='json') for e in value[1].spans],complete=value[1].complete,
        reasons=value[1].reasons,revision_ms=value[3]) for text,value in cache.items()]
    write(output/'revised-recordings.json',records)
    write(output/'environment.json',dict(python=platform.python_version(),platform=platform.platform(),
        processor=platform.processor(),evaluation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    lines=['# Сравнение экстракции: baseline и отдельная ревизия','',
        'Замороженные реальные выходы GLiNER + regex. Веса, порог и эталон не менялись. Линкер не запускается.','',
        '| Этап | Версия | Сессии | TP | FP | FN | Precision | Recall | F2 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for stage,items in [('До фильтров',summary['raw']),('Одинаковый успешный срез',
            {name:summary['common_filtered'][name] for name in runs})]:
        for name,data in items.items():
            m=data['micro']; count=len(docs) if stage=='До фильтров' else len(common)
            fmt=lambda x:'N/A' if x is None else f'{x:.2%}'
            lines.append(f'| {stage} | {name} | {count} | {m["tp"]} | {m["fp"]} | {m["fn"]} | {fmt(m["precision"])} | {fmt(m["recall"])} | {fmt(m["f2"])} |')
    lines.extend(['','| Версия | Завершённые сессии после обработки | Ошибки |','|---|---:|---:|'])
    for name,item in summary['filtered'].items():
        lines.append(f'| {name} | {item["completed_sessions"]} | {len(item["errors"])} |')
    command='.\\.venv-gliner\\Scripts\\python.exe -X utf8 tools/evaluate_adapter_revision.py'
    if snapshot!=ROOT/'data/experiments/random-linker-250-2026-11-24/snapshot.json':
        command+=f' --snapshot {snapshot.relative_to(ROOT).as_posix()} --recordings {recording_dir.relative_to(ROOT).as_posix()} --output {output.relative_to(ROOT).as_posix()}'
    lines.extend(['','Метрики по типам и ошибки — в summary.json.', '',summary['tuning_scope'],'',
        f'Повтор: `{command}` из корня worktree.',''])
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'data/experiments/adapter-revision-2026-11-25')
    parser.add_argument('--snapshot',type=Path,help='Отдельный замороженный срез')
    parser.add_argument('--recordings',type=Path,help='Каталог реальных предсказаний для этого среза')
    parser.add_argument('--record-fresh',action='store_true',help='Выполнить новый локальный инференс GLiNER')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/'data'):
        parser.error('Результаты должны храниться в data/')
    if args.record_fresh and (args.recordings is None or not args.recordings.resolve().is_relative_to(ROOT/'data')):
        parser.error('Для нового инференса нужен --recordings внутри data/')
    result=evaluate(args.output.resolve(),args.snapshot.resolve() if args.snapshot else None,
        args.recordings.resolve() if args.recordings else None,args.record_fresh)
    print(json.dumps(dict(raw=result['raw'],filtered={k:dict(completed_sessions=v['completed_sessions'],
        errors=len(v['errors']),micro=v['extraction']['micro']) for k,v in result['filtered'].items()}),ensure_ascii=False))
