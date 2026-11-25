"""Последовательный возобновляемый пилот Gemma-линера на синтетических gold-спанах."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import statistics
import sys
import time
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools'),str(ROOT)]
from annotation_contract import assess
from annotation_calculator import Calculator
from compare_annotation_models import groups
from evaluate_random_linker import ReferenceDetector, alerts, pairwise, read, write, extraction, key, record_detector
from session_pii.detectors import DetectorSettings
from session_pii.components import EmptyDetector
from session_pii.gemma_linker import ChatCompletion, GemmaLinker, GemmaSettings, PROMPT
from session_pii.models import Message, Verdict
from session_pii.pipeline import Pipeline
from session_pii.adapter_revision import RevisedAdapter, VERSION as ADAPTER_VERSION

FAMILIES=['different_people','shared_phone_personal_email','shared_address',
          'unknown_contact_pair','late_owner_remention','service_contacts']


def matched_linker_metrics(docs, predictions):
    """Линковка только exact type-aware спанов; пропуски NER считаются отдельно."""
    gold_subset, predicted_subset = [], []
    matched = total = 0
    for doc, pred in zip(docs, predictions):
        by_key = {key(entity):entity for entity in pred['entities']}
        rows = [entity for entity in doc['entities'] if key(entity) in by_key]
        gold_subset.append({**doc, 'entities':rows})
        predicted_subset.append([by_key[key(entity)] for entity in rows])
        matched += len(rows)
        total += len(doc['entities'])
    return dict(**pairwise(gold_subset, predicted_subset),
        matched_mentions=matched, gold_mentions_in_completed_sessions=total,
        scope='pairwise on exact type-aware matched spans in completed sessions; NER misses and extras scored separately')


def load_key(path):
    if os.environ.get('RADEON_API_KEY'):
        return
    if path:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.lstrip().startswith('#') and '=' in line:
                key,value=line.split('=',1)
                if key.strip()=='RADEON_API_KEY':
                    os.environ['RADEON_API_KEY']=value.strip().strip('"').strip("'")


def expected_alert(record,calculator):
    if 'expected_first_alert' in record:
        index=record['expected_first_alert']
        if index is not None and (type(index) is not int or not 1<=index<=len(record['gold']['messages'])):
            raise ValueError('Invalid authored first-alert index')
        return dict(message_index=index) if index is not None else None
    return assess(record['gold'],calculator)['first_alert']


def summarize(output,source):
    state=read(output/'manifest.json')
    by_id={r['session_id']:r['gold'] for r in source['records']}
    completed=[r for r in state['sessions'] if r['status']=='complete']
    docs=[by_id[r['session_id']] for r in completed]
    predictions=[read(output/r['path']) for r in completed]
    calculator=Calculator()
    records={r['session_id']:r for r in source['records']}
    expected=[expected_alert(records[doc['session_id']],calculator) for doc in docs]
    result=dict(model=state['model'],selected_sessions=len(state['selected_ids']),completed_sessions=len(completed),
        attempted_sessions=len(state['sessions']),failures=[r for r in state['sessions'] if r['status']!='complete'],
        scope='isolated linker with gold spans; coverage reported separately; not end-to-end NER or throughput benchmark; no gold owners supplied',
        pairwise=matched_linker_metrics(docs,predictions) if state.get('detector_mode') else pairwise(docs,[r['entities'] for r in predictions]),
        owner_groups_exact_sessions=sum(groups(doc)==groups(pred) for doc,pred in zip(docs,predictions)),
        runtime_alerts=alerts([r['message_index'] if r else None for r in expected],[r['first_alert'] for r in predictions]))
    result['cases']=[dict(session_id=doc['session_id'],owner_groups_exact=groups(doc)==groups(pred),
        gold_first_alert=gold['message_index'] if gold else None,predicted_first_alert=pred['first_alert'])
        for doc,pred,gold in zip(docs,predictions,expected)]
    timings=[read(p)['request_seconds'] for p in output.glob('*/attempt-*/call-*-timing.json')]
    result['api']=dict(completed_calls=len(timings),p50_seconds=statistics.median(timings) if timings else None,
        max_seconds=max(timings) if timings else None)
    if state.get('detector_mode'):
        raw=read(output/'raw-detector-entities.json')
        all_docs=[by_id[sid] for sid in state['selected_ids']]
        result.update(scope='real GLiNER+regex outputs -> service filtering -> Gemma -> runtime risk; completed sessions only for alerts; failures reported separately',
            detector_mode=state['detector_mode'],
            raw_extraction_all_sessions=extraction(all_docs,[raw[sid] for sid in state['selected_ids']]),
            filtered_extraction_completed_sessions=extraction(docs,[p['entities'] for p in predictions]),
            ner_latency=read(output/'ner-latency.json'))
        result['pairwise']['aggregation']+='; matched-span subset only'
        values=[step['seconds'] for p in predictions for step in p.get('step_timings',[])]
        result['pipeline_latency']=dict(messages=len(values),median_seconds=statistics.median(values) if values else None,
            max_seconds=max(values) if values else None,
            scope='current in-process pipeline including live Gemma API on completed sessions; frozen NER replay, not fresh NER or HTTP latency')
    write(output/'summary.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/experiments/random-linker-250-2026-11-24/snapshot.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--config',type=Path,default=ROOT/'configs/linker-gemma-baseline.json')
    parser.add_argument('--ner-recordings',type=Path,help='Проверенный сохранённый реальный выход GLiNER+regex вместо эталонных спанов')
    parser.add_argument('--adapter-revision',action='store_true',help='Применить отдельную зафиксированную ревизию адаптера к реальным выходам NER')
    parser.add_argument('--families',nargs='+',default=FAMILIES)
    parser.add_argument('--all-sessions',action='store_true',help='Все сессии снимка вместо малого пилота')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'data'):
        parser.error('Результаты сохраняются внутри игнорируемого data/')
    source=read(args.input)
    explicitly_synthetic=source.get('synthetic_only') is True
    legacy_synthetic=source.get('total_source_sessions')==1000 and all(r['session_id'].startswith('synthetic-') for r in source['records'])
    if not (explicitly_synthetic or legacy_synthetic) or any(not re.fullmatch(r'[A-Za-z0-9_-]+',r['session_id']) for r in source['records']):
        parser.error('Нужен явно синтетический снимок авторского корпуса')
    if args.adapter_revision and not args.ner_recordings:
        parser.error('--adapter-revision требует --ner-recordings')
    if args.report_only:
        if read(output/'manifest.json')['input_sha256']!=hashlib.sha256(args.input.read_bytes()).hexdigest():
            parser.error('Исходный снимок отличается от входа сохранённого запуска')
        print(json.dumps(summarize(output,source),ensure_ascii=False))
        return
    load_key(args.env_file)
    settings=GemmaSettings.from_env(args.config)
    selected=list(source['records']) if args.all_sessions else [
        next(r for r in source['records'] if r['family']==family) for family in args.families]
    identity=dict(input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
        prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),selected_ids=[r['session_id'] for r in selected])
    recorded=None
    recordings=[]
    if args.ner_recordings:
        detector_settings=DetectorSettings(mode='gliner',model_dir=ROOT/'outputs/gliner-prepared')
        recorded,recordings=record_detector([r['gold'] for r in source['records']],detector_settings,args.ner_recordings,reuse=True)
        identity.update(detector_mode='recorded-real-gliner-regex',
            detector_recordings_sha256=hashlib.sha256((args.ner_recordings/'detector-recordings.json').read_bytes()).hexdigest(),
            detector_identity=read(args.ner_recordings/'detector-recordings-identity.json'))
        if args.adapter_revision:
            identity.update(adapter_revision=ADAPTER_VERSION,
                adapter_revision_sha256=hashlib.sha256((ROOT/'src/session_pii/adapter_revision.py').read_bytes()).hexdigest())
            identity['detector_mode']+='+'+ADAPTER_VERSION
            recorded=RevisedAdapter(recorded)
    if output.exists():
        if not args.resume:
            parser.error('Каталог существует; для продолжения используйте --resume')
        state=read(output/'manifest.json')
        if any(state[k]!=v for k,v in identity.items()):
            parser.error('Конфиг, промпт или данные изменились; нужен новый запуск')
    else:
        output.mkdir(parents=True)
        state=dict(**identity,model=settings.model,started_at=datetime.now(timezone.utc).isoformat(),sessions=[])
        write(output/'manifest.json',state)
    write(output/'input-snapshot.json',source)
    if recorded is not None:
        raw={r['session_id']:[dict(message_id=m['id'],type=e.type,start=e.start,end=e.end)
            for i,m in enumerate(r['gold']['messages'],1)
            for e in recorded.detect(Message(message_id=i,text=m['text'])).spans] for r in selected}
        write(output/'raw-detector-entities.json',raw)
        values=[r['latency_ms'] for r in recordings]
        write(output/'ner-latency.json',dict(unique_texts=len(values),median_ms=statistics.median(values),
            max_ms=max(values),scope='original CPU inference timings from frozen real detector recording; not current API or HTTP latency'))
    complete={r['session_id'] for r in state['sessions'] if r['status']=='complete'}
    network=ChatCompletion(settings)
    last_start=-float('inf')
    for item in selected:
        sid=item['session_id']
        if sid in complete:
            continue
        attempts=[r for r in state['sessions'] if r['session_id']==sid]
        if attempts and attempts[-1].get('status')=='failed' and attempts[-1].get('error_type')=='ValueError' and (
                attempts[-1].get('error','').startswith(('Unresolved cross-type overlap','Нужен полный email',
                    'Нужен явно указанный возраст'))):
            # Эти ошибки зависят от неизменённых NER-спанов, а не от сети.
            print(f'{sid}: preserved deterministic contract failure',flush=True)
            continue
        attempt=output/sid/f'attempt-{len(attempts)+1:02d}'
        attempt.mkdir(parents=True,exist_ok=False)
        calls=0
        def request(body):
            nonlocal calls,last_start
            calls+=1
            prefix=f'call-{calls:02d}'
            write(attempt/(prefix+'-request.json'),body)
            time.sleep(max(0,3.2-(time.monotonic()-last_start)))
            last_start=time.monotonic()
            start=time.perf_counter()
            raw=network(body)
            elapsed=time.perf_counter()-start
            write(attempt/(prefix+'-response.json'),raw)
            write(attempt/(prefix+'-timing.json'),dict(request_seconds=elapsed))
            print(f'{sid}: call {calls} completed in {elapsed:.2f}s',flush=True)
            return raw
        doc=item['gold']
        pipeline=Pipeline(recorded if recorded is not None else ReferenceDetector(doc),EmptyDetector(),GemmaLinker(settings,complete=request))
        facts,verdict,entities,history=(),Verdict(),[],()
        record=dict(session_id=sid,family=item.get('family','source-defined'),status='running',attempt=len(attempts)+1)
        state['sessions'].append(record)
        write(output/'manifest.json',state)
        step_timings=[]
        i=0
        try:
            for i,m in enumerate(doc['messages'],1):
                message=Message(message_id=i,text=m['text'])
                step_start=time.perf_counter()
                step=pipeline.process(message,facts,verdict,session_id=sid,previous_messages=history)
                step_timings.append(dict(message_id=i,seconds=time.perf_counter()-step_start))
                facts,verdict=step.facts,step.verdict
                history=(history+(message,))[-settings.context_messages:] if settings.context_messages else ()
                entities.extend(dict(message_id=m['id'],start=e.start,end=e.end,type=e.type,
                    person_id=e.person_id,service_contact=e.service_contact) for e in step.entities)
            prediction=dict(session_id=sid,entities=entities,first_alert=verdict.first_alert_message_id,step_timings=step_timings)
            path=attempt/'prediction.json'
            write(path,prediction)
            record.update(status='complete',path=path.relative_to(output).as_posix())
        except Exception as exc:
            record.update(status='failed',error_type=type(exc).__name__,http_status=getattr(exc,'code',None),message_index=i,error=str(exc)[:300])
            write(attempt/'partial-prediction.json',dict(session_id=sid,entities=entities,
                first_alert=verdict.first_alert_message_id,step_timings=step_timings))
            write(output/'manifest.json',state)
            if isinstance(exc,(HTTPError,URLError,TimeoutError,OSError)):
                print(f'{sid}: transport failure; stopped without repeated requests',flush=True)
                break
        write(output/'manifest.json',state)
        print(f'{sid}: {record["status"]}',flush=True)
    state['last_updated_at']=datetime.now(timezone.utc).isoformat()
    write(output/'manifest.json',state)
    print(json.dumps(summarize(output,source),ensure_ascii=False))


if __name__=='__main__':
    main()
