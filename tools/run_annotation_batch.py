"""Параллельные независимые сессии; внутри сессии последовательный существующий runner."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from annotation_contract import validate
from compare_annotation_models import compare
from audit_sequential_annotation import audit
from benchmarks.pii_smoke.regex_detector import valid_inn, valid_snils, valid_luhn

CHECKS = {'individual_inn':valid_inn,'snils':valid_snils,'bank_card':valid_luhn}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def checksum_issues(doc):
    texts = {m['id']:m['text'] for m in doc['messages']}
    return [dict(entity_id=e['id'],type=e['type'],reason='invalid_checksum')
            for e in doc['entities'] if e['type'] in CHECKS and
            not CHECKS[e['type']](texts[e['message_id']][e['start']:e['end']])]


def prepare(source, output, batch_size):
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        raise ValueError('Нужен новый каталог внутри data/')
    sessions = read(source/'sessions.json')['sessions']
    gold = read(source/'gold.json')['annotations']
    if len({s['session_id'] for s in sessions}) != len(sessions):
        raise ValueError('Неуникальные сессии')
    for doc in gold:
        validate(doc)
        if checksum_issues(doc):
            raise ValueError('Неверная checksum в авторской разметке')
    checks = read(source/'gold-assessments.json')['checks']
    prefixes = read(source/'gold-prefixes.json')['steps']
    output.mkdir(parents=True)
    jobs = []
    for start in range(0,len(sessions),batch_size):
        batch = output/f'batch-{start//batch_size+1:03d}'
        part = batch/'source'
        part.mkdir(parents=True)
        subset = sessions[start:start+batch_size]
        ids = {s['session_id'] for s in subset}
        for name,value in [('sessions.json',dict(synthetic_only=True,sessions=subset)),
                           ('gold.json',dict(annotations=[d for d in gold if d['session_id'] in ids])),
                           ('gold-assessments.json',dict(checks=[d for d in checks if d['session_id'] in ids])),
                           ('gold-prefixes.json',dict(steps=[d for d in prefixes if d['session_id'] in ids])),
                           ('manifest.json',{**read(source/'manifest.json'),'sessions':len(subset)})]:
            write(part/name,value)
        jobs.append(dict(source=str(part),run=str(batch/'run'),sessions=len(subset),status='pending'))
    state = dict(source=str(source),source_sha256=hashlib.sha256((source/'sessions.json').read_bytes()).hexdigest(),
                 sessions=len(sessions),planned_requests=sum(len(s['messages']) for s in sessions),
                 jobs=jobs,author_gold_validated=True,gold_passed_to_model=False)
    write(output/'batch-manifest.json',state)
    return state


def report(output):
    state = read(output/'batch-manifest.json')
    comparisons, audits, issues, differences = [], [], [], []
    statuses = Counter()
    completed_steps = attempted_requests = 0
    for job in state['jobs']:
        source, run = Path(job['source']), Path(job['run'])
        manifest_file = run/'manifest.json'
        if not manifest_file.exists():
            continue
        manifest = read(manifest_file)
        statuses.update(r['status'] for r in manifest['records'])
        completed_steps += sum(s['status']=='valid' for s in manifest['steps'])
        attempted_requests += manifest['api_requests']
        # Незавершённый запрос нельзя считать проверенным или отрицательным.
        if 'finished_at' not in manifest:
            continue
        comparison = compare(source,run)
        protocol = audit(source,run)
        write(run.parent/'comparison.json',comparison)
        write(run.parent/'protocol-audit.json',protocol)
        comparisons.append(comparison)
        audits.append(protocol)
        for number,record in enumerate(manifest['records'],1):
            if record['status']=='valid':
                doc = read(run/f'{number:02d}-annotation.json')
                for issue in checksum_issues(doc):
                    issues.append(dict(session_id=record['session_id'],**issue))
        def first_signature(value):
            return (value['message_id'],value['reason']) if value else None
        differences.extend(dict(batch=run.parent.name,**case) for case in comparison['cases']
                           if case['status']!='valid' or not case['all_spans_and_ownership_exact']
                           or first_signature(case['gold_first_alert'])!=first_signature(case['predicted_first_alert']))
    result = dict(sessions_planned=state['sessions'],planned_requests=state['planned_requests'],
        statuses=dict(statuses),finished_batches=len(comparisons),total_batches=len(state['jobs']),
        completed_steps=completed_steps,attempted_requests=attempted_requests,
        completed_sessions=statuses['valid'],
        all_batches_finished=all(j['status'] in {'finished','failed','skipped_after_http_error'} for j in state['jobs']),
        exact_spans_and_owners=sum(c['all_spans_and_ownership_exact_sessions'] for c in comparisons),
        compared_valid_sessions=sum(c['valid_sessions'] for c in comparisons),
        audited_requests=sum(a['requests_verified'] for a in audits),
        checksum_issues=issues,disagreements=differences,human_review_status='pending',
        reference_status='assistant_authored_templates_not_independent_human_gold')
    write(output/'overview.json',result)
    text = ['# Проверка синтетического корпуса', '',
            'Авторская разметка подготовлена до Gemma; проверка человеком ожидается.', '',
            f"Сессий запланировано: {result['sessions_planned']}; статусы: {dict(statuses)}.",
            f"Завершённых батчей: {len(comparisons)}/{len(state['jobs'])}.",
            f"Полностью совпали спаны и владельцы: {result['exact_spans_and_owners']}/{result['compared_valid_sessions']} валидных сессий.",
            f"Запросов проверено на отсутствие будущего и эталона: {result['audited_requests']}.",
            f"Невалидных checksum в выходе модели: {len(issues)}. Они сохранены отдельно и не приняты как проверенный gold.",
            '', 'Все расхождения и ошибки: overview.json. Ошибки запросов не превращаются в отрицательные метки.']
    (output/'overview.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return {k:v for k,v in result.items() if k not in {'checksum_issues','disagreements'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--model',default='Gemma-4-31B-IT')
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--http-retries',type=int,default=0)
    parser.add_argument('--retry-delay',type=float,default=30)
    parser.add_argument('--batch-size',type=int,default=25)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--run-prepared',action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.report_only:
        print(json.dumps(report(output),ensure_ascii=False))
        return
    if not 1 <= args.workers <= 8 or args.batch_size < 1:
        parser.error('workers: 1..8; batch-size >=1')
    state = read(output/'batch-manifest.json') if args.run_prepared else prepare(args.source.resolve(),output,args.batch_size)
    if args.prepare_only:
        print(json.dumps(dict(sessions=state['sessions'],requests=state['planned_requests'],batches=len(state['jobs']))))
        return
    env = os.environ.copy()
    if not env.get('RADEON_API_KEY') and args.env_file:
        for line in args.env_file.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name,value = line.split('=',1)
                if name.strip()=='RADEON_API_KEY':
                    env['RADEON_API_KEY'] = value.strip().strip('"').strip("'")
    if not env.get('RADEON_API_KEY'):
        parser.error('Ключ RADEON_API_KEY не настроен; ни одного API-запроса не отправлено')
    if any(j['status']!='pending' for j in state['jobs']):
        parser.error('Этот запуск уже начат; для отчёта используйте --report-only')
    state.update(started_at=datetime.now(timezone.utc).isoformat(),workers=args.workers,requested_model=args.model)
    lock, stopped = threading.Lock(), threading.Event()
    def execute(job):
        with lock:
            if stopped.is_set():
                job['status']='skipped_after_http_error'
                write(output/'batch-manifest.json',state)
                return
            job['status']='running'
            write(output/'batch-manifest.json',state)
        run = Path(job['run'])
        command = [sys.executable,'-X','utf8',str(ROOT/'tools/run_sequential_annotation.py'),
                   '--input',str(Path(job['source'])/'sessions.json'),'--output',str(run),
                   '--model',args.model,'--timeout',str(args.timeout),'--max-tokens','4000','--delay','1',
                   '--http-retries',str(args.http_retries),'--retry-delay',str(args.retry_delay)]
        with (run.parent/'runner.log').open('w',encoding='utf-8') as log:
            code = subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
        manifest = read(run/'manifest.json') if (run/'manifest.json').exists() else {}
        if any(r['status']=='http_error' for r in manifest.get('records',[])):
            stopped.set()
        with lock:
            job.update(status='finished' if code==0 else 'failed',exit_code=code)
            write(output/'batch-manifest.json',state)
        print(json.dumps(dict(batch=run.parent.name,status=job['status'])),flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(execute,j) for j in state['jobs']]):
            future.result()
    state['finished_at']=datetime.now(timezone.utc).isoformat()
    write(output/'batch-manifest.json',state)
    print(json.dumps(report(output),ensure_ascii=False))


if __name__=='__main__':
    # При запуске tools/*.py пакет benchmarks находится в родительском каталоге.
    main()
