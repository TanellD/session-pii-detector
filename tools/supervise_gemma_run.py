"""Фоновый запуск оценки с ограниченным продолжением после транспортных ошибок."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--ner-recordings',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,default=ROOT/'../../.env')
    parser.add_argument('--starts',type=int,default=4)
    parser.add_argument('--wait-for-extraction-summary',type=Path,help='Начать LLM-прогон после сохранения сравнения двух NER')
    args=parser.parse_args()
    run=args.run.resolve()
    if not run.is_relative_to(ROOT/'data') or not 1<=args.starts<=5:
        parser.error('Нужен каталог в data/ и от одного до пяти запусков')
    (run/'logs').mkdir(parents=True,exist_ok=True)
    if args.wait_for_extraction_summary:
        status=dict(status='waiting_for_extraction',started_at=datetime.now(timezone.utc).isoformat(),
                    prerequisite=str(args.wait_for_extraction_summary.resolve()))
        (run/'supervisor.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        while not args.wait_for_extraction_summary.exists():
            time.sleep(5)
        comparison=json.loads(args.wait_for_extraction_summary.read_text(encoding='utf-8'))
        source=json.loads(args.input.read_text(encoding='utf-8'))
        if comparison['total_sessions']!=len(source['records']):
            raise ValueError('Размер сравнения NER отличается от входа LLM-прогона')
    previous=[int(p.name.split('-')[1]) for p in (run/'logs').glob('start-*-stdout.log')]
    offset=max(previous,default=0)
    for batch_attempt in range(1,args.starts+1):
        attempt=offset+batch_attempt
        command=[sys.executable,'-X','utf8','-u',str(ROOT/'tools/evaluate_gemma_linker.py'),
            '--input',str(args.input.resolve()),'--ner-recordings',str(args.ner_recordings.resolve()),
            '--adapter-revision','--all-sessions','--output',str(run/'results'),'--env-file',str(args.env_file.resolve())]
        if (run/'results/manifest.json').exists():
            command.append('--resume')
        status=dict(start=attempt,max_starts=offset+args.starts,batch_start=offset+1,
                    started_at=datetime.now(timezone.utc).isoformat(),status='running')
        (run/'supervisor.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        with (run/f'logs/start-{attempt:02d}-stdout.log').open('w',encoding='utf-8') as out, (run/f'logs/start-{attempt:02d}-stderr.log').open('w',encoding='utf-8') as err:
            result=subprocess.run(command,cwd=ROOT,stdout=out,stderr=err)
        manifest=run/'results/manifest.json'
        state=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else None
        last=state['sessions'][-1] if state and state['sessions'] else {}
        transient=last.get('status')=='failed' and (last.get('error_type') in {
            'RemoteDisconnected','URLError','TimeoutError','ConnectionResetError','ConnectionError','OSError'}
            or last.get('http_status')==429 or (last.get('http_status') or 0)>=500)
        status.update(status='backoff' if transient and batch_attempt<args.starts else 'stopped',
                      exit_code=result.returncode,transient_transport_error=transient)
        (run/'supervisor.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
        if result.returncode or not transient or batch_attempt==args.starts:
            return
        time.sleep(30)


if __name__=='__main__':
    main()
