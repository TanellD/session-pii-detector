"""Новый прогон только незавершённых сессий; прежние запросы/ответы не изменяются."""
import argparse
import hashlib
import json
from pathlib import Path

from run_annotation_batch import prepare, write

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def build(source,previous,output):
    source,output=source.resolve(),output.resolve()
    previous=[p.resolve() for p in previous] if isinstance(previous,list) else [previous.resolve()]
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        raise ValueError('Нужен новый каталог внутри data/')
    valid=set()
    for prior in previous:
        old=read(prior/'batch-manifest.json')
        if 'finished_at' not in old:
            raise ValueError('Предыдущий прогон ещё работает')
        for job in old['jobs']:
            manifest=Path(job['run'])/'manifest.json'
            if manifest.exists():
                for record in read(manifest)['records']:
                    if record['status']=='valid':
                        valid.add(record['session_id'])
    all_sessions=read(source/'sessions.json')['sessions']
    remaining=[s for s in all_sessions if s['session_id'] not in valid]
    if not remaining:
        raise ValueError('Все сессии уже завершены')
    ids={s['session_id'] for s in remaining}
    part=output/'source'
    part.mkdir(parents=True)
    write(part/'sessions.json',dict(synthetic_only=True,sessions=remaining))
    for name,field in [('gold.json','annotations'),('gold-assessments.json','checks'),('gold-prefixes.json','steps')]:
        write(part/name,{field:[r for r in read(source/name)[field] if r['session_id'] in ids]})
    manifest=read(source/'manifest.json')
    manifest.update(sessions=len(remaining),messages=sum(len(s['messages']) for s in remaining),
                    recovery_of=[str(p) for p in previous],successful_sessions_not_repeated=sorted(valid))
    write(part/'manifest.json',manifest)
    state=prepare(part,output/'batches',25)
    write(output/'recovery-manifest.json',dict(source=str(source),previous=[str(p) for p in previous],
        source_sha256=hashlib.sha256((source/'sessions.json').read_bytes()).hexdigest(),
        skipped_successful_sessions=sorted(valid),remaining_sessions=len(remaining),
        partial_failed_sessions_restarted_from_first_message=True,old_responses_preserved=True))
    return dict(skipped_successful_sessions=len(valid),remaining_sessions=len(remaining),planned_requests=state['planned_requests'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--previous',type=Path,required=True,action='append')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.source,args.previous,args.output)))
