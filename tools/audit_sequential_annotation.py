"""Проверить сохранённые запросы: нет будущего или эталона, прежние предсказания заморожены."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit(source_dir,run):
    source = {s['session_id']:s for s in read(source_dir/'sessions.json')['sessions']}
    reference_path=source_dir/'gold-prefixes.json'
    gold = {(s['session_id'],s['prefix_length']):s['assessment'] for s in read(reference_path)['steps']} if reference_path.exists() else {}
    manifest = read(run/'manifest.json')
    if manifest['input_sha256'] != hashlib.sha256((source_dir/'sessions.json').read_bytes()).hexdigest():
        raise ValueError('Другой исходный набор')
    results = []
    for number,record in enumerate(manifest['records'],1):
        previous = None
        session = source[record['session_id']]
        steps = [s for s in manifest['steps'] if s['session_id']==session['session_id']]
        for n,step in enumerate(steps,1):
            stem = f'{number:02d}-m{n:02d}'
            request = read(run/f'{stem}-request.json')
            if (set(request) != {'model','messages','max_tokens','temperature','stream','response_format'} or
                    len(request['messages']) != 2 or
                    [m['role'] for m in request['messages']] != ['system','user'] or
                    request['model'] != manifest['requested_model'] or
                    hashlib.sha256(request['messages'][0]['content'].encode()).hexdigest() != manifest['prompt_sha256']):
                raise ValueError('Изменён системный промпт или структура запроса')
            payload = json.loads(request['messages'][1]['content'])
            expected = {'session_id':session['session_id'],'messages':session['messages'][:n]}
            if set(payload) != {'session','previous_annotation','current_message_id','message_selection'}:
                raise ValueError('В запросе лишние поля')
            if (payload['session'] != expected or payload['previous_annotation'] != previous or
                    payload['current_message_id'] != session['messages'][n-1]['id'] or payload['message_selection'] != 'user_only'):
                raise ValueError('Запрос нарушает доступный префикс или содержит другое предыдущее состояние')
            item = {'session_id':session['session_id'],'message_id':step['message_id'],
                    'status':step['status'],'request_prefix_verified':True}
            if step['status']=='valid':
                current = read(run/f'{stem}-annotation.json')
                if current['messages'] != expected['messages']:
                    raise ValueError('Сообщения результата изменены')
                if previous and current['entities'][:len(previous['entities'])] != previous['entities']:
                    raise ValueError('Прежние сущности или принадлежность изменились')
                result = read(run/f'{stem}-assessment.json')
                pred = result['first_alert']
                signature = lambda x:[x['message_id'],x['reason']] if x else None
                item.update(frozen_history_verified=True,predicted_first_alert=signature(pred))
                reference=gold.get((session['session_id'],n))
                if reference is not None:
                    target=reference['first_alert']
                    item.update(gold_first_alert=signature(target),first_alert_matches_author=signature(target)==signature(pred))
                previous = current
            else:
                item.update(error_type=step.get('error_type'),http_status=step.get('http_status'))
                if n!=len(steps):
                    raise ValueError('После ошибки в этой сессии запросы продолжились')
            results.append(item)
    return {'requests_verified':len(results),'valid_steps':sum(s['status']=='valid' for s in results),
            'all_requests_have_only_allowed_prefix':True,'all_valid_steps_keep_history':True,
            'gold_not_supplied':True,'steps':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    result=audit(args.source,args.run)
    if args.check:
        if read(args.output)!=result:
            raise ValueError('Сохранённый аудит отличается')
    else:
        if args.output.exists():
            parser.error('Файл уже существует')
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='steps'},ensure_ascii=False))


if __name__=='__main__':
    main()
