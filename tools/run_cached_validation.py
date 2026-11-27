"""Возобновляемый validation: один NER, кэш Gemma, затем локальный подбор риска."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Lock, local
import time
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools'), str(ROOT)]
from evaluate_gemma_linker import load_key, matched_linker_metrics
from evaluate_random_linker import alerts, extraction, recording_identity
from session_pii.adapter_revision_v2 import RevisedAdapter
from session_pii.components import DetectionResult, EmptyDetector
from session_pii.detectors import AdapterDetector, DetectorSettings
from session_pii.gemma_linker import ChatCompletion, GemmaLinker, GemmaSettings, PROMPT
from session_pii.models import Message, Span, Verdict
from session_pii.pipeline import Pipeline
from session_pii.risk import RiskEvaluator, RiskPolicy


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class CachedDetector:
    def __init__(self, directory):
        self.directory = directory

    def detect(self, message):
        row = read(self.directory/(hashlib.sha256(message.text.encode()).hexdigest()+'.json'))
        return DetectionResult(tuple(Span.model_validate_json(json.dumps(e)) for e in row['spans']), row['complete'], tuple(row['reasons']))


def prepare_ner(source, run, update):
    docs = [r['gold'] for r in source['records']]
    settings = DetectorSettings(mode='gliner', model_dir=ROOT/'outputs/gliner-prepared')
    texts = list(dict.fromkeys(m['text'] for d in docs for m in d['messages']))
    directory = run/'ner/by-text'
    missing = [text for text in texts if not (directory/(hashlib.sha256(text.encode()).hexdigest()+'.json')).exists()]
    update(stage='ner', ner_total=len(texts), ner_completed=len(texts)-len(missing))
    if missing:
        import torch
        torch.set_num_threads(read(settings.config_path)['threads'])
        detector = AdapterDetector(settings)
        original_predict = detector._predict

        def capture(text):
            key = hashlib.sha256(text.encode()).hexdigest()
            target = run/'ner/native-gliner'/(key+'.json')
            if target.exists():
                return read(target)['entities']
            values = original_predict(text)
            write(target, dict(text_sha256=key, entities=values))
            return values

        detector._predict = capture
        for index, text in enumerate(missing, 1):
            start = time.monotonic()
            result = detector.detect(Message(message_id=1, text=text))
            key = hashlib.sha256(text.encode()).hexdigest()
            write(directory/(key+'.json'), dict(text_sha256=key, spans=[s.model_dump(mode='json') for s in result.spans],
                                                complete=result.complete, reasons=result.reasons, seconds=time.monotonic()-start))
            if index % 10 == 0 or index == len(missing):
                update(ner_completed=len(texts)-len(missing)+index)
                print(f'NER {len(texts)-len(missing)+index}/{len(texts)}', flush=True)
    revised = RevisedAdapter(CachedDetector(directory))
    raw = {doc['session_id']: [dict(message_id=m['id'], start=s.start, end=s.end, type=s.type)
                               for i, m in enumerate(doc['messages'], 1)
                               for s in revised.detect(Message(message_id=i, text=m['text'])).spans] for doc in docs}
    write(run/'ner/extraction.json', extraction(docs, [raw[d['session_id']] for d in docs]))
    update(stage='linker', ner_completed=len(texts))
    return revised


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--no-tune', action='store_true')
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(ROOT/'data') or not 1 <= args.workers <= 4:
        parser.error('Need data/ run directory and 1..4 workers')
    source = read(args.input)
    if source.get('synthetic_only') is not True or not source.get('records'):
        parser.error('Explicitly synthetic records required')
    if any(r.get('split') != 'validation' for r in source['records']):
        parser.error('Calibration accepts validation only; held-out test must remain unused')
    detector_settings = DetectorSettings(mode='gliner', model_dir=ROOT/'outputs/gliner-prepared')
    config_path = ROOT/'configs/linker-gemma-baseline.json'
    identity = dict(input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    detector=recording_identity([r['gold'] for r in source['records']], detector_settings),
                    linker_config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(), prompt_sha256=digest(PROMPT),
                    code={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                          ['src/session_pii/adapter_revision.py', 'src/session_pii/adapter_revision_v2.py',
                           'src/session_pii/normalization.py', 'src/session_pii/pipeline.py',
                           'src/session_pii/gemma_linker.py', 'src/session_pii/detectors.py',
                           'benchmarks/adapter/adapter.py', 'benchmarks/adapter/adapter_rules.py',
                           'benchmarks/pii_smoke/regex_detector.py']})
    if run.exists():
        if not args.resume or read(run/'identity.json') != identity:
            parser.error('Existing run requires --resume and identical input/models/prompt/implementation')
    else:
        run.mkdir(parents=True)
        write(run/'identity.json', identity)
        write(run/'input-snapshot.json', source)
        write(run/'baseline-policy.json', RiskPolicy.load().model_dump())
    policy = RiskPolicy.model_validate(read(run/'baseline-policy.json'))
    extra = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
             ['benchmarks/adapter/_rules_impl.py', 'benchmarks/adapter/adapter_gliner.py',
              'benchmarks/adapter/adapter_contract.py', 'src/session_pii/risk.py']}
    if (run/'dependency-guard.json').exists() and read(run/'dependency-guard.json') != extra:
        parser.error('Adapter implementation or risk code changed; create a new run')
    write(run/'dependency-guard.json', extra)
    lock = Lock()
    status = read(run/'status.json') if (run/'status.json').exists() else dict(sessions_total=len(source['records']), sessions_completed=0)

    def update(**values):
        with lock:
            status.update(values, updated_at=datetime.now(timezone.utc).isoformat())
            write(run/'status.json', status)
            lines = ['# Validation: NER v2 + Gemma и подбор риска', '',
                     f'Обновлено: {status["updated_at"]}', '',
                     f'Этап: **{status.get("stage", "initializing")}**', '',
                     f'NER: {status.get("ner_completed", 0)}/{status.get("ner_total", "?")} уникальных текстов.',
                     f'Сессии: {status.get("sessions_completed", 0)}/{status["sessions_total"]}.',
                     f'Ошибки сессий: {status.get("session_failures", 0)}.', '',
                     'Все модельные ответы сохранены. Подбор весов и порогов использует локальный replay.',
                     'Test не используется; рабочая политика автоматически не заменяется.', '']
            (run/'live-progress.md').write_text('\n'.join(lines), encoding='utf-8')

    detector = prepare_ner(source, run, update)
    load_key(ROOT/'../../.env')
    settings = replace(GemmaSettings.from_env(config_path), timeout_seconds=180)
    rate_lock, cache_lock = Lock(), Lock()
    key_locks, last_start = {}, [-1e9]
    request_context = local()

    def cached_request(body):
        key = digest(body)
        request_context.key = key
        target = run/'llm-cache'/(key+'.json')
        with cache_lock:
            request_lock = key_locks.setdefault(key, Lock())
        with request_lock:
            if target.exists():
                saved = read(target)
                if saved['request'] != body:
                    raise ValueError('Cached request identity mismatch')
                request_context.response_hash = digest(saved['response'])
                return saved['response']
            for attempt in range(4):
                try:
                    with rate_lock:
                        time.sleep(max(0, 3.2-(time.monotonic()-last_start[0])))
                        last_start[0] = time.monotonic()
                    started = time.monotonic()
                    response = ChatCompletion(settings)(body)
                    write(target, dict(request=body, response=response, seconds=time.monotonic()-started))
                    request_context.response_hash = digest(response)
                    return response
                except (HTTPError, URLError, TimeoutError, OSError) as error:
                    write(run/'transport-errors'/(key+f'-{time.time_ns()}.json'),
                          dict(error_type=type(error).__name__, http_status=getattr(error, 'code', None), attempt=attempt+1))
                    if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                        raise
                    if attempt == 3:
                        raise
                    time.sleep(min(30, 5*2**attempt))

    def evaluate(row):
        sid = row['session_id']
        target = run/'predictions'/(sid+'.json')
        if target.exists():
            return read(target)
        pipeline = Pipeline(detector, EmptyDetector(), GemmaLinker(settings, complete=cached_request), RiskEvaluator(policy))
        facts, previous, history, entities = (), Verdict(), (), []
        try:
            for i, m in enumerate(row['gold']['messages'], 1):
                message = Message(message_id=i, text=m['text'])
                for step_attempt in range(3):
                    request_context.key = None
                    try:
                        step = pipeline.process(message, facts, previous, session_id=sid, previous_messages=history)
                        break
                    except ValueError:
                        key = request_context.key
                        if key is None or step_attempt == 2:
                            raise
                        # Invalid model output is preserved, never silently reused
                        # forever. Deterministic pre-linker contract errors do not
                        # make another API call.
                        with cache_lock:
                            request_lock = key_locks.setdefault(key, Lock())
                        with request_lock:
                            cached = run/'llm-cache'/(key+'.json')
                            if cached.exists() and digest(read(cached)['response']) == request_context.response_hash:
                                rejected = run/'rejected-llm-cache'/(key+f'-{time.time_ns()}.json')
                                rejected.parent.mkdir(parents=True, exist_ok=True)
                                cached.replace(rejected)
                facts, previous = step.facts, step.verdict
                history = (history+(message,))[-settings.context_messages:] if settings.context_messages else ()
                entities.extend(dict(message_id=m['id'], start=e.start, end=e.end, type=e.type,
                                     person_id=e.person_id, service_contact=e.service_contact) for e in step.entities)
            prediction = dict(session_id=sid, status='complete', entities=entities, first_alert=previous.first_alert_message_id)
            write(target, prediction)
            return prediction
        except Exception as error:
            result = dict(session_id=sid, status='failed', error_type=type(error).__name__, error=str(error)[:300],
                          message_index=i, entities=entities)
            write(run/'failures'/(sid+'.json'), result)
            return result

    completed = {}
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(evaluate, row) for row in source['records']]
        for future in as_completed(futures):
            result = future.result()
            if result['status'] == 'complete':
                completed[result['session_id']] = result
            else:
                failures.append(result)
            update(sessions_completed=len(completed), session_failures=len(failures))
            print(f'Sessions {len(completed)}/{len(source["records"])}; failures {len(failures)}', flush=True)
    selected = [r for r in source['records'] if r['session_id'] in completed]
    predictions = [completed[r['session_id']] for r in selected]
    docs = [r['gold'] for r in selected]
    metrics = dict(completed=len(completed), total=len(source['records']), failures=failures,
                   alerts=alerts([r['expected_first_alert'] for r in selected], [p['first_alert'] for p in predictions]),
                   extraction=extraction(docs, [p['entities'] for p in predictions]),
                   linker=matched_linker_metrics(docs, predictions), scope='completed sessions; failures separate, never negative labels')
    write(run/'baseline-metrics.json', metrics)
    if not args.no_tune:
        update(stage='offline_risk_tuning')
        command = [sys.executable, '-X', 'utf8', str(ROOT/'tools/calibrate_cached_risk.py'), '--run', str(run)]
        if failures:
            command.append('--allow-incomplete')
        result = subprocess.run(command, cwd=ROOT)
        update(stage=('complete_with_failures' if failures else 'complete') if result.returncode == 0 else 'needs_attention')
    else:
        update(stage='inference_complete')


if __name__ == '__main__':
    main()
