"""Выбрать русские синтетические клиентские префиксы CONFIDE и случайные подвыборки ревью."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re

from audit_confide_for_annotation import REVISION, ROOT, TURN

DEFAULT = ROOT/'data/experiments/russian-session-dataset-2026-11-19'


def user_messages(text, limit):
    markers = list(TURN.finditer(text))
    messages, origins = [], []
    for i,marker in enumerate(markers):
        if (marker.group(1) or marker.group(2)) != 'К':
            continue
        start = marker.end()
        end = markers[i+1].start() if i+1<len(markers) else len(text)
        block = text[start:end]
        # В части файлов время записано отдельной строкой перед следующей репликой.
        block = re.sub(r'(?:\s*\*\*\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?\*\*)+\s*$','',block)
        left = len(block)-len(block.lstrip())
        value = block.strip()
        if not value:
            continue
        start += left
        messages.append({'id':f'm{len(messages)+1}','text':value})
        origins.append({'message_id':messages[-1]['id'],'source_turn_index':i+1,'source_role':'К',
                        'start':start,'end':start+len(value)})
        if len(messages)==limit:
            break
    return messages,origins


def russian_share(text):
    letters = [c for c in text if c.isalpha()]
    return sum('а'<=c.lower()<='я' or c.lower()=='ё' for c in letters)/len(letters) if letters else 0


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--seed',type=int,default=20261119)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT/'data'):
        parser.error('Нужен новый каталог внутри data/')
    cache=ROOT/'data/external/confide-synthetic'/REVISION
    source=cache/'data/sessions-ru/pii-eval-ru.jsonl'
    rows=[json.loads(s) for s in source.read_text(encoding='utf-8').splitlines() if s.strip()]
    eligible=[]
    for row in sorted(rows,key=lambda x:x['doc_id']):
        messages,origins=user_messages(row['text'],4)
        ratio=russian_share(' '.join(m['text'] for m in messages))
        if row['doc_id'].startswith('ru-') and len(messages)==4 and ratio>=0.7:
            eligible.append((row,messages,origins,ratio))
    rng=random.Random(args.seed)
    initial=[r for r in eligible if r[0]['doc_id'].endswith('-s01')]
    later=[r for r in eligible if not r[0]['doc_id'].endswith('-s01')]
    chosen=sorted(initial+rng.sample(later,4),key=lambda r:r[0]['doc_id'])
    sessions,provenance=[],[]
    for i,(row,messages,origins,ratio) in enumerate(chosen,1):
        sid=f'ru-session-{i:02d}'
        if any(row['text'][m['start']:m['end']]!=messages[j]['text'] for j,m in enumerate(origins)):
            raise ValueError('Не совпали исходные границы')
        sessions.append({'session_id':sid,'messages':messages})
        provenance.append({'session_id':sid,'source_doc_id':row['doc_id'],'source_profile':row['client'],
                           'source_split':row['split'],'cyrillic_letter_share':ratio,'messages':origins})
    review_order=[s['session_id'] for s in sessions]
    rng.shuffle(review_order)
    plan={'seed':args.seed,'sampling_unit':'complete_selected_prefix',
          'blind_session_ids':review_order[:2],'label_visible_session_ids':review_order[2:4],
          'reserve_order':review_order[4:],
          'reviewer':'current_interactive_llm_not_independent_human',
          'blind_protocol':'freeze annotations before model run; no source gold or model outputs viewed for selected text',
          'visible_protocol':'inspect Gemma spans, links and derived alerts after run; do not replace raw predictions'}
    output.mkdir(parents=True)
    write(output/'sessions.json',{'synthetic_only':True,'sessions':sessions})
    write(output/'provenance.json',{'source':'https://github.com/glebis/confide','revision':REVISION,
         'license':'CC-BY-4.0 for own synthetic data; MIT code',
         'original_annotations_used':False,'origins':provenance})
    write(output/'review-plan.json',plan)
    write(output/'manifest.json',{'message_selection':'user_only','synthetic_only':True,
        'source_kind':'author_declared_synthetic_RU_only','seed':args.seed,
        'selection':'all first sessions of profiles plus seeded sample of later sessions; first 4 client turns',
        'language_filter':'RU source namespace plus Cyrillic fraction among letters >= 0.7 for prefix; not a universal language detector',
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_documents':len(rows),'eligible_documents':len(eligible),'sessions':len(sessions),
        'messages':sum(len(s['messages']) for s in sessions),
        'synthetic_provenance_not_independently_verified':True,
        'scope':'short user-only prefixes; not full original sessions; exploratory dev/test use, not held-out evaluation'})
    def markdown(selected,title):
        lines=[f'# {title}','','Только клиентские реплики, без исходных и модельных меток.','']
        for session in sessions:
            if session['session_id'] in selected:
                lines += [f"## {session['session_id']}",'']
                lines += [f"**{m['id']}** {m['text']}\n" for m in session['messages']]
        return '\n'.join(lines)
    (output/'sessions-for-review.md').write_text(markdown({s['session_id'] for s in sessions},'Русские сессии без меток'),encoding='utf-8')
    (output/'blind-review-input.md').write_text(markdown(set(plan['blind_session_ids']),'Случайная подвыборка для проверки вслепую'),encoding='utf-8')
    print(json.dumps({'source_documents':len(rows),'eligible_documents':len(eligible),'sessions':len(sessions),
        'messages':sum(len(s['messages']) for s in sessions),'review_plan':plan},ensure_ascii=False))


if __name__=='__main__':
    main()
