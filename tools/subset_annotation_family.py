"""Подготовить отдельный повтор семейства без изменения исходного набора и эталона."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--family',required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        parser.error('Нужен новый каталог внутри data/')
    read = lambda name: json.loads((args.source/name).read_text(encoding='utf-8'))
    original = read('sessions.json')
    if original.get('synthetic_only') is not True:
        parser.error('Источник должен явно содержать synthetic_only=true; нельзя переобозначать реальные данные')
    checks = [c for c in read('gold-assessments.json')['checks'] if c['family']==args.family]
    ids = {c['session_id'] for c in checks}
    if not ids:
        parser.error('Нет выбранного семейства')
    sessions = [s for s in original['sessions'] if s['session_id'] in ids]
    payloads = {'sessions.json':dict(synthetic_only=True,sessions=sessions),
                'gold.json':dict(annotations=[d for d in read('gold.json')['annotations'] if d['session_id'] in ids]),
                'gold-assessments.json':dict(checks=checks),
                'gold-prefixes.json':dict(steps=[s for s in read('gold-prefixes.json')['steps'] if s['session_id'] in ids]),
                'manifest.json':dict(message_selection='user_only',synthetic_only=True,
                                     scope='targeted repeat, not independent new data',family=args.family,
                                     source_sessions_sha256=hashlib.sha256((args.source/'sessions.json').read_bytes()).hexdigest(),
                                     source_gold_sha256=hashlib.sha256((args.source/'gold.json').read_bytes()).hexdigest())}
    output.mkdir(parents=True)
    for name,value in payloads.items():
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(sessions=len(sessions),messages=sum(len(s['messages']) for s in sessions))))


if __name__=='__main__':
    main()
