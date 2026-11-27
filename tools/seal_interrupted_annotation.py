"""Зафиксировать прерванный прогон после остановки его процессов, сохранив исходные манифесты."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from run_annotation_batch import write

ROOT=Path(__file__).resolve().parents[1]


def seal(root):
    root=root.resolve()
    if not root.is_relative_to(ROOT/'data'):
        raise ValueError('Прогон должен находиться внутри data/')
    moment=datetime.now(timezone.utc)
    timestamp=moment.isoformat()
    suffix=moment.strftime('%Y%m%dT%H%M%S%fZ')
    def backup(path):
        target=path.with_name(path.stem+f'-before-interruption-{suffix}'+path.suffix)
        with target.open('xb') as stream:
            stream.write(path.read_bytes())
    path=root/'batch-manifest.json'
    batch=json.loads(path.read_text(encoding='utf-8'))
    if 'finished_at' in batch:
        return dict(status='already_finished',root=str(root))
    backup(path)
    changed=0
    for job in batch['jobs']:
        run=Path(job['run'])/'manifest.json'
        if run.exists():
            manifest=json.loads(run.read_text(encoding='utf-8'))
            if 'finished_at' not in manifest:
                backup(run)
                for record in manifest['records']:
                    if record['status']=='requested':
                        record.update(status='invalid_or_failed',error_type='InterruptedForRecovery')
                for step in manifest['steps']:
                    if step['status']=='requested':
                        step.update(status='invalid_or_failed',error_type='InterruptedForRecovery')
                manifest.update(finished_at=timestamp,interrupted_at=timestamp,stop_reason='processes_stopped_for_recovery')
                write(run,manifest)
                changed+=1
        if job['status']=='running':
            job.update(status='failed',stop_reason='processes_stopped_for_recovery')
        elif job['status']=='pending':
            job.update(status='skipped_after_http_error',stop_reason='not_started_before_recovery')
    batch.update(finished_at=timestamp,interrupted_at=timestamp,stop_reason='processes_stopped_for_recovery')
    write(path,batch)
    return dict(status='sealed_interrupted',root=str(root),changed_run_manifests=changed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,action='append',required=True)
    parser.add_argument('--processes-stopped',action='store_true',required=True)
    args=parser.parse_args()
    print(json.dumps([seal(root) for root in args.run]))
