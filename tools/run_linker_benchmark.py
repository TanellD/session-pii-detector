"""Последовательная оценка сменного JSON-линкера на сохранённой экстракции."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools'), str(ROOT)]
from session_pii.components import DetectionResult, EmptyDetector
from session_pii.gemma_linker import GemmaLinker, GemmaSettings
from session_pii.models import Message, Span, Verdict
from session_pii.pipeline import Pipeline
from session_pii.risk import RiskEvaluator, RiskPolicy
from calibrate_cached_risk import measured
from evaluate_gemma_linker import matched_linker_metrics
from evaluate_random_linker import extraction


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    """Уникальный tmp и ограниченные повторы при блокировке файла Windows/IDE."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.05*(attempt+1))


class FrozenDetector:
    def __init__(self, row):
        self.messages = row['messages']

    def detect(self, message):
        row = self.messages[message.message_id-1]
        if message.text != row['text']:
            raise ValueError('Frozen NER text mismatch')
        return DetectionResult(tuple(Span.model_validate_json(json.dumps({k: v for k, v in s.items() if k != 'value'}))
                                     for s in row['spans']), row['complete'], tuple(row['reasons']))


def gold_input(doc):
    return dict(session_id=doc['session_id'], messages=[dict(text=m['text'], complete=True, reasons=[],
                spans=[dict(type=e['type'], start=e['start'], end=e['end']) for e in doc['entities']
                       if e['message_id'] == m['id']]) for m in doc['messages']])


def summarize(run):
    source = read(run/'input-snapshot.json')
    rows, predictions, failures = [], [], []
    for row in source['records']:
        sid = row['session_id']
        if (run/'predictions'/(sid+'.json')).exists():
            rows.append(row)
            predictions.append(read(run/'predictions'/(sid+'.json')))
        elif (run/'failures'/(sid+'.json')).exists():
            failures.append(read(run/'failures'/(sid+'.json')))
    report = dict(total=len(source['records']), completed=len(rows), failed=len(failures),
                  failures=failures, scope='completed sessions only; failures never TN')
    if rows:
        expected = [dict(expected=r['expected_first_alert'], uncertain=r['final_uncertain']) for r in rows]
        report.update(alerts=measured(expected, [p['first_alert'] for p in predictions]),
                      extraction=extraction([r['gold'] for r in rows], [p['entities'] for p in predictions]),
                      linker=matched_linker_metrics([r['gold'] for r in rows], predictions))
        calls = [c for p in predictions for c in p['calls']]
        fresh = [c['seconds'] for c in calls if not c['cache_hit']]
        report['timing'] = dict(calls=len(calls), fresh_calls=len(fresh),
                               median_seconds=statistics.median(fresh) if fresh else None,
                               p95_seconds=sorted(fresh)[min(len(fresh)-1, int(.95*len(fresh)))] if fresh else None,
                               scope='successful-session linker calls; loading excluded; serial, not saturation RPS')
    write(run/'baseline-metrics.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--split', choices=['train', 'validation', 'test'], default='validation')
    parser.add_argument('--mode', choices=['gold', 'real-ner'], default='real-ner')
    parser.add_argument('--linker', required=True, help='module:function; callable(request_dict) -> assignments_dict')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--context-messages', type=int, default=3)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    run, package = args.run.resolve(), args.package.resolve()
    if not run.is_relative_to(ROOT/'data'):
        parser.error('Run must be under data/')
    manifest = read(package/'manifest.json')
    for name, digest in manifest['files'].items():
        if sha(package/name) != digest:
            raise ValueError('Package file checksum mismatch: '+name)
    module_name, function_name = args.linker.split(':', 1)
    module = importlib.import_module(module_name)
    predict = getattr(module, function_name)
    policy_path = args.policy or package/'baseline-policy.json'
    policy = RiskPolicy.model_validate(read(policy_path))
    source_path = package/args.split/'targets/snapshot.json'
    source = read(source_path)
    inputs = {r['session_id']: r for r in read(package/args.split/'inputs/sessions.json')}
    ner = {r['session_id']: r for r in (json.loads(line) for line in
            (package/args.split/'inputs/ner.jsonl').read_text(encoding='utf-8').splitlines())}
    identity = dict(package_sha256=sha(package/'manifest.json'), split=args.split, mode=args.mode,
                    linker=args.linker, linker_module_sha256=sha(Path(module.__file__)), policy_sha256=sha(policy_path),
                    context_messages=args.context_messages, runner_sha256=sha(Path(__file__)),
                    runtime_sha256={name: sha(ROOT/name) for name in ['src/session_pii/pipeline.py',
                        'src/session_pii/normalization.py', 'src/session_pii/gemma_linker.py', 'src/session_pii/risk.py']})
    if (run/'identity.json').exists():
        if not args.resume or read(run/'identity.json') != identity:
            raise ValueError('Use --resume with unchanged identity or a new run directory')
    write(run/'identity.json', identity)
    write(run/'baseline-policy.json', policy.model_dump())
    write(run/'input-snapshot.json', source)
    run.with_name(run.name+'-worker.pid').write_text(str(os.getpid()), encoding='ascii')
    done = failed = 0
    for row in source['records']:
        sid, calls = row['session_id'], []
        target = run/'predictions'/(sid+'.json')
        if target.exists():
            done += 1
            continue
        facts, previous, history, entities = (), Verdict(), (), []

        def complete(body):
            request = json.loads(body['messages'][-1]['content'])
            key = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            cache = run/'request-cache'/(key+'.json')
            hit = cache.exists()
            start = time.perf_counter()
            if hit:
                saved = read(cache)
                if saved['request'] != request:
                    raise ValueError('Request cache collision')
                response = saved['response']
                elapsed = 0.0
            else:
                response = predict(request)
                elapsed = time.perf_counter()-start
                write(cache, dict(request=request, response=response, seconds=elapsed))
            calls.append(dict(seconds=elapsed, cache_hit=hit, message_id=request['message']['message_id']))
            return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(response)))])

        linker = GemmaLinker(GemmaSettings(api_key='offline-unused', base_url='http://localhost',
                            context_messages=args.context_messages), complete=complete)
        # Runtime JSON contract/parser is reused; no Gemma API is constructed or called.
        detector = FrozenDetector(ner[sid] if args.mode == 'real-ner' else gold_input(row['gold']))
        pipeline = Pipeline(detector, EmptyDetector(), linker, RiskEvaluator(policy))
        index = 0
        try:
            for index, message in enumerate(inputs[sid]['messages'], 1):
                msg = Message(message_id=index, text=message['text'])
                step = pipeline.process(msg, facts, previous, session_id=sid, previous_messages=history)
                if not step.verdict.coverage_complete:
                    raise ValueError('Incomplete pipeline analysis: '+str(step.verdict.incomplete_reasons))
                facts, previous = step.facts, step.verdict
                history = (history+(msg,))[-args.context_messages:] if args.context_messages else ()
                entities.extend(dict(message_id=message['id'], start=e.start, end=e.end, type=e.type,
                                     person_id=e.person_id, service_contact=e.service_contact) for e in step.entities)
            write(target, dict(session_id=sid, status='complete', entities=entities,
                               first_alert=previous.first_alert_message_id, calls=calls))
            done += 1
        except Exception as error:
            write(run/'failures'/(sid+'.json'), dict(session_id=sid, error_type=type(error).__name__,
                  error=str(error)[:400], message_index=index, calls=calls))
            failed += 1
        write(run/'status.json', dict(stage='linker', sessions_total=len(source['records']),
              sessions_completed=done, session_failures=failed, dataset_mode=args.mode,
              updated_at=datetime.now(timezone.utc).isoformat()))
        if (done+failed) % 25 == 0:
            print(f'{done}/{len(source["records"])} complete; {failed} failed', flush=True)
    report = summarize(run)
    write(run/'status.json', dict(stage='complete_with_failures' if report['failed'] else 'complete',
          sessions_total=report['total'], sessions_completed=report['completed'], session_failures=report['failed'],
          updated_at=datetime.now(timezone.utc).isoformat(), dataset_mode=args.mode))
    print(json.dumps({k: report[k] for k in ['total', 'completed', 'failed']}))


if __name__ == '__main__':
    main()
