"""Слепое по меткам ревью диалогов: сохранённый промпт, сырой ответ, возобновление."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'src'), str(ROOT)]
from evaluate_gemma_linker import load_key
from session_pii.gemma_linker import ChatCompletion, GemmaSettings


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    tmp.replace(path)


def summarize(output):
    state = read(output/'manifest.json')
    collected, errors = {}, []
    for entry in state['batches']:
        if entry.get('parsed_path'):
            collected.update({r['review_id']:r for r in read(output/entry['parsed_path'])})
        elif entry['status'] == 'failed':
            errors.append(entry)
    mapping = state['mapping']
    rows = list(collected.values())
    results = [dict(**r, **mapping[r['review_id']]) for r in rows]
    summary = dict(updated_at=datetime.now(timezone.utc).isoformat(), model=state['model'],
        completed=len(rows), total=len(mapping), failed_batches=len(errors),
        verdicts=dict(Counter(r['verdict'] for r in rows)),
        issue_categories=dict(Counter(i['category'] for r in rows for i in r['issues'])),
        difficulty_confusion={d: dict(Counter(r['estimated_difficulty'] for r in results if r['planned_difficulty']==d))
            for d in ('easy','medium','hard')}, reviews=results, failures=errors)
    write(output/'summary.json', summary)
    lines = ['# Проверка текстов пилота', '', f'Обновлено: {summary["updated_at"]}.', '',
        f'Проверено моделью: **{len(rows)}/{len(mapping)}**. Неуспешных пакетов: {len(errors)}.', '',
        'Рецензент не получает эталон, запланированную сложность и владельцев. '
        'Это дополнительное мнение модели, не проверка истинности разметки и не замер сервиса.', '',
        '| Сессия | План | Оценка сложности | Вердикт | Замечания |', '|---|---|---|---|---|']
    for row in sorted(results, key=lambda r:r['session_id']):
        notes = '; '.join(f'{i["category"]}: m{i["message_id"]} «{i["quote"]}» — {i["reason"]}' for i in row['issues'])
        notes = notes.replace('|','/').replace('\n',' ')
        lines.append(f'| {row["session_id"]} | {row["planned_difficulty"]} | {row["estimated_difficulty"]} | {row["verdict"]} | {notes or row["reason"]} |')
    (output/'live-progress.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return summary


def parse(raw, batch):
    choices = raw.get('choices', [])
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        raise ValueError('Incomplete response')
    result = json.loads(choices[0]['message']['content'])
    rows = result['reviews']
    by_id = {r['review_id']:r for r in batch}
    accepted, rejected, seen = [], [], set()
    for row in rows:
        row = deepcopy(row)
        try:
            rid = row['review_id']
            if rid not in by_id or rid in seen:
                raise ValueError('Unknown or duplicate review ID')
            seen.add(rid)
            if row['verdict'] not in {'pass','revise'} or row['estimated_difficulty'] not in {'easy','medium','hard'}:
                raise ValueError('Invalid verdict')
            texts = {(m['message_id'] if 'message_id' in m else int(m['id'][1:])):m['text'] for m in by_id[rid]['messages']}
            repairs = []
            for issue in row['issues']:
                if issue['severity'] not in {'minor','major'} or not issue['quote']:
                    raise ValueError('Invalid issue')
                if issue['message_id'] not in texts or issue['quote'] not in texts[issue['message_id']]:
                    matches = [mid for mid,text in texts.items() if issue['quote'] in text]
                    if len(matches) != 1:
                        raise ValueError('Issue quote not uniquely grounded')
                    repairs.append(dict(field='message_id',old=issue['message_id'],new=matches[0],basis='unique_exact_quote_match'))
                    issue['message_id'] = matches[0]
            if repairs:
                row['validation_repairs'] = repairs
            accepted.append(row)
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append(dict(review_id=row.get('review_id'),error=str(exc)))
    rejected.extend(dict(review_id=rid,error='Missing review') for rid in by_id if rid not in seen)
    return accepted, rejected


def schema():
    issue = dict(type='object',additionalProperties=False,
        required=['category','severity','message_id','quote','reason'],properties=dict(
            category=dict(type='string',enum=['logic','missing_context','speaker','unnatural_disclosure','benchmark_hint','grammar','artificial_ease']),
            severity=dict(type='string',enum=['minor','major']),message_id=dict(type='integer'),quote=dict(type='string'),reason=dict(type='string')))
    review = dict(type='object',additionalProperties=False,
        required=['review_id','verdict','estimated_difficulty','reason','issues'],properties=dict(
            review_id=dict(type='string'),verdict=dict(type='string',enum=['pass','revise']),
            estimated_difficulty=dict(type='string',enum=['easy','medium','hard']),reason=dict(type='string'),
            issues=dict(type='array',items=issue)))
    return dict(type='object',additionalProperties=False,required=['reviews'],properties=dict(reviews=dict(type='array',items=review)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,default=ROOT/'../../.env')
    parser.add_argument('--batch-size',type=int,default=5)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--timeout-seconds',type=float,default=120)
    parser.add_argument('--report-only',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'data'):
        parser.error('Output must be in data/')
    if args.report_only:
        print(json.dumps({k:v for k,v in summarize(output).items() if k not in {'reviews','failures'}},ensure_ascii=False,indent=2))
        return
    source=read(args.input)
    if source.get('synthetic_only') is not True:
        parser.error('Only explicitly synthetic inputs')
    prompt_text=(ROOT/'docs/data/dialogue-quality-review-prompt.md').read_text(encoding='utf-8')
    prompt=prompt_text.split('## Системное задание\n',1)[1].split('## Использование',1)[0].strip()
    load_key(args.env_file)
    settings=replace(GemmaSettings.from_env(),max_tokens=4096,timeout_seconds=args.timeout_seconds)
    identity=dict(input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),model=settings.model,batch_size=args.batch_size)
    shuffled=list(source['records'])
    random.Random(20261126).shuffle(shuffled)
    requests=[dict(review_id=f'q{i:03d}',messages=r['gold']['messages']) for i,r in enumerate(shuffled,1)]
    if output.exists():
        if not args.resume:
            parser.error('Use --resume for an existing run')
        state=read(output/'manifest.json')
        if any(state.get(k)!=v for k,v in identity.items()):
            parser.error('Resume identity mismatch')
    else:
        output.mkdir(parents=True)
        state=dict(**identity,mapping={q['review_id']:dict(session_id=r['session_id'],planned_difficulty=r['difficulty'])
            for q,r in zip(requests,shuffled)},batches=[])
        write(output/'manifest.json',state)
        (output/'system-prompt.txt').write_text(prompt,encoding='utf-8')
    # Salvage valid individual reviews from older partial/failed batches.
    for entry in state['batches']:
        if entry.get('parsed_path'):
            continue
        directory=output/entry['batch_id']/f'attempt-{entry["attempt"]:02d}'
        if (directory/'raw-response.json').exists():
            try:
                old=read(directory/'request.json')
                accepted,rejected=parse(read(directory/'raw-response.json'),json.loads(old['messages'][1]['content'])['sessions'])
                path=(directory/'parsed-recovered.json').relative_to(output).as_posix()
                write(output/path,accepted)
                entry.update(status='complete' if not rejected else 'partial',parsed_path=path,rejected=rejected)
            except (ValueError,KeyError,TypeError):
                pass
    write(output/'manifest.json',state)
    report=summarize(output)
    done={r['review_id'] for r in report['reviews']}
    pending=[r for r in requests if r['review_id'] not in done]
    sequence=max([int(b['batch_id'].rsplit('-',1)[1]) for b in state['batches']] or [0])

    def execute(batch, entry):
        prefix=Path(entry['batch_id'])/'attempt-01'
        supplied=[dict(review_id=r['review_id'],messages=[dict(message_id=int(m['id'][1:]),text=m['text']) for m in r['messages']]) for r in batch]
        format_schema=schema()
        item=format_schema['properties']['reviews']['items']
        item['properties']['review_id']['enum']=[r['review_id'] for r in batch]
        item['properties']['issues']['items']['properties']['message_id']['enum']=list(range(1,max(len(r['messages']) for r in batch)+1))
        format_schema['properties']['reviews'].update(minItems=len(batch),maxItems=len(batch))
        body=dict(model=settings.model,temperature=0,max_tokens=settings.max_tokens,stream=False,
            messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps(dict(sessions=supplied),ensure_ascii=False))],
            response_format=dict(type='json_schema',json_schema=dict(name='dialogue_reviews',strict=True,schema=format_schema)))
        write(output/prefix/'request.json',body)
        started=time.monotonic()
        try:
            raw=ChatCompletion(settings)(body)
            write(output/prefix/'raw-response.json',raw)
            parsed,rejected=parse(raw,batch)
            path=(prefix/'parsed.json').as_posix()
            write(output/path,parsed)
            entry.update(status='complete' if not rejected else 'partial',parsed_path=path,rejected=rejected,seconds=time.monotonic()-started)
        except Exception as exc:
            entry.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:500],seconds=time.monotonic()-started)
        return entry

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures=[]
        for offset in range(0,len(pending),args.batch_size):
            sequence+=1
            entry=dict(batch_id=f'batch-{sequence:03d}',status='running',attempt=1,request_protocol='integer_message_ids_v2',timeout_seconds=settings.timeout_seconds)
            state['batches'].append(entry)
            write(output/'manifest.json',state)
            futures.append(executor.submit(execute,pending[offset:offset+args.batch_size],entry))
        for future in as_completed(futures):
            entry=future.result()
            write(output/'manifest.json',state)
            report=summarize(output)
            print(f'{entry["batch_id"]}: {entry["status"]}; reviewed {report["completed"]}/{report["total"]}',flush=True)


if __name__=='__main__':
    main()
