"""Обновлять локальный статус двух прогонов и сохранить их итоговое сравнение без API-вызовов."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from run_annotation_batch import report, write
from prepare_annotation_recovery import build as prepare_recovery


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def progress(roots):
    steps=Counter()
    latest_records={}
    source_sessions={}
    batches=[]
    requests=0
    models=set()
    for root in roots:
        batch=read(root/'batch-manifest.json')
        batches.append(batch)
        for session in read(Path(batch['source'])/'sessions.json')['sessions']:
            if session['session_id'] in source_sessions and source_sessions[session['session_id']]!=session:
                raise ValueError('В повторном прогоне изменился исходный текст')
            source_sessions[session['session_id']]=session
        for job in batch['jobs']:
            path=Path(job['run'])/'manifest.json'
            if path.exists():
                run=read(path)
                for record in run['records']:
                    if latest_records.get(record['session_id'])!='valid':
                        latest_records[record['session_id']]=record['status']
                steps.update(s['status'] for s in run['steps'])
                models.add(run['requested_model'])
                requests+=run['api_requests']
    records=Counter(latest_records.values())
    return dict(planned_sessions=len(source_sessions),planned_requests=sum(len(s['messages']) for s in source_sessions.values()),
                completed_sessions=records['valid'],session_statuses=dict(records),step_statuses=dict(steps),
                attempted_requests=requests,models=sorted(models),processing_finished=all('finished_at' in b for b in batches),
                coverage_complete=records['valid']==len(source_sessions),
                batch_statuses=dict(Counter(j['status'] for b in batches for j in b['jobs'])))


def update(first,second,output):
    data=read(output/'comparison.json')
    pa,pb=progress(first),progress(second)
    finished=pa['processing_finished'] and pb['processing_finished']
    status=dict(updated_at=datetime.now(timezone.utc).isoformat(),first=pa,second=pb,
                automated_processing_finished=finished,llm_coverage_complete=pa['coverage_complete'] and pb['coverage_complete'],
                same_annotator_controlled_comparison=False,independent_human_review=False)
    if finished:
        status['first_annotation_report']=[report(root) for root in first]
        status['second_annotation_report']=[report(root) for root in second]
    write(output/'llm-comparison-status.json',status)
    text=[(output/'comparison.md').read_text(encoding='utf-8'),
          '\n## Состояние внешней проверки\n',
          f"Обновлено: {status['updated_at']}.\n",
          f"Первый набор: {pa['completed_sessions']}/{pa['planned_sessions']} завершённых сессий; статусы {pa['session_statuses']}.\n",
          f"Второй набор: {pb['completed_sessions']}/{pb['planned_sessions']} завершённых сессий; статусы {pb['session_statuses']}.\n",
          'Прогон завершён. Ошибки и расхождения требуют разбора.\n' if finished else 'Прогоны продолжаются. Полные модельные метрики ещё не готовы.\n',
          'Валидный JSON не означает принятую эталонную разметку; спорные владельцы и checksum проверяются отдельно.\n']
    (output/'combined-review.md').write_text('\n'.join(text),encoding='utf-8')
    return status


def launch_recovery(roots,label,output,env_file,number):
    recovery=output/f'recovery-{label}-{number:02d}'
    source=Path(read(roots[0]/'batch-manifest.json')['source'])
    prepare_recovery(source,roots,recovery)
    target=recovery/'batches'
    command=[sys.executable,'-X','utf8',str(Path(__file__).resolve().parent/'run_annotation_batch.py'),
             '--output',str(target),'--run-prepared','--workers','1',
             '--model',progress(roots)['models'][0],'--timeout','300','--http-retries','3','--retry-delay','60']
    if env_file:
        command.extend(['--env-file',str(env_file)])
    with (recovery/'runner.log').open('w',encoding='utf-8') as log, (recovery/'runner-errors.log').open('w',encoding='utf-8') as errors:
        process=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[1],stdout=log,stderr=errors,
                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    roots.append(target)
    return dict(run=str(target),pid=process.pid,source=label)


def restore_watch_state(first,second,output):
    saved_path=output/'watch-state.json'
    saved=read(saved_path) if saved_path.exists() else {}
    def restore_roots(field,current):
        roots={}
        for value in [*saved.get(field,[]),*current]:
            path=Path(value).resolve()
            roots[str(path)]=path
        return list(roots.values())
    first=restore_roots('first_runs',first)
    second=restore_roots('second_runs',second)
    recoveries=Counter(saved.get('recoveries',{}))
    write(saved_path,{**saved,'first_runs':[str(p) for p in first],
                     'second_runs':[str(p) for p in second],'recoveries':dict(recoveries),
                     'watcher_started_at':datetime.now(timezone.utc).isoformat()})
    return first,second,recoveries


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-run',type=Path,required=True,action='append')
    parser.add_argument('--second-run',type=Path,required=True,action='append')
    parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--recover-failures',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--max-recoveries',type=int,default=3)
    args=parser.parse_args()
    args.first_run,args.second_run,recoveries=restore_watch_state(args.first_run,args.second_run,args.comparison)
    while True:
        try:
            status=update(args.first_run,args.second_run,args.comparison)
            print(json.dumps(dict(first_complete=status['first']['completed_sessions'],second_complete=status['second']['completed_sessions'],
                                  finished=status['automated_processing_finished'])),flush=True)
            launched=False
            if args.recover_failures:
                for label,roots in [('first',args.first_run),('second',args.second_run)]:
                    item=status[label]
                    if item['processing_finished'] and not item['coverage_complete'] and recoveries[label]<args.max_recoveries:
                        recoveries[label]+=1
                        result=launch_recovery(roots,label,args.comparison,args.env_file,recoveries[label])
                        write(args.comparison/'watch-state.json',dict(first_runs=[str(p) for p in args.first_run],
                            second_runs=[str(p) for p in args.second_run],recoveries=dict(recoveries),last_launch=result))
                        print(json.dumps(dict(recovery_started=result)),flush=True)
                        launched=True
            if launched:
                continue
            if not args.watch or status['automated_processing_finished']:
                break
        except json.JSONDecodeError:
            # Старый runner сохраняет манифест без atomic rename; чтение может попасть в запись.
            if not args.watch:
                raise
        time.sleep(30)
