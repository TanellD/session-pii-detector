"""Зафиксировать слепую проверку и собрать сводку русских сессий без повторных API-запросов."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random

from annotation_contract import assess
from compare_annotation_models import key,groups,score
from run_compatible_annotation_pilot import materialize_quotes
from audit_sequential_annotation import audit
from run_sequential_annotation import merge_step

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'data/experiments/russian-session-dataset-2026-11-19'
RUN=ROOT/'data/experiments/radeon/2026-11-19-russian-dataset-gemma-01'
VERIFY=False
REFRESH=False
DERIVED={'reviewed-references.json','sequential-audit.json','overview.json','overview.md','gemma-annotations-for-review.md'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path,value):
    if VERIFY:
        if read(path)!=value:
            raise ValueError('Сохранённый результат отличается: '+str(path))
        return
    if path.exists() and not (REFRESH and path.name in DERIVED):
        raise ValueError('Файл уже существует: '+str(path))
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def write_text(path,value):
    if VERIFY:
        if path.read_text(encoding='utf-8')!=value:
            raise ValueError('Сохранённый текст отличается: '+str(path))
    else:
        if path.exists() and not (REFRESH and path.name in DERIVED):
            raise ValueError('Файл уже существует: '+str(path))
        path.write_text(value,encoding='utf-8')


def strata(dataset,run):
    manifest=read(run/'manifest.json')
    if any(r['status']=='requested' for r in manifest['records']):
        raise ValueError('Прогон ещё выполняется')
    eligible={'without_model_entities':[],'with_model_entities':[]}
    for number,record in enumerate(manifest['records'],1):
        for step in (s for s in manifest['steps'] if s['session_id']==record['session_id'] and s['status']=='valid'):
            length=int(step['message_id'][1:])
            pred=read(run/f'{number:02d}-m{length:02d}-annotation.json')
            current=[e for e in pred['entities'] if e['message_id']==step['message_id']]
            group='with_model_entities' if current else 'without_model_entities'
            eligible[group].append({'session_id':record['session_id'],'message_id':step['message_id'],
                'prefix':pred['messages'],'model_entities':current})
    rule=read(dataset/'stratum-review-rule.json')
    rng=random.Random(rule['seed'])
    sample={name:rng.sample(rows,min(rule['messages_per_stratum'],len(rows))) for name,rows in eligible.items()}
    write(dataset/'stratum-review-sample.json',{'rule':rule,'eligible_counts':{k:len(v) for k,v in eligible.items()},'sample':sample})
    print(json.dumps(sample,ensure_ascii=False,indent=2))


def materialize(source,selection):
    entities=[]
    for i,row in enumerate(selection['selections'],1):
        m,q,t,p=row[:4]
        occurrence=row[4] if len(row)==5 else 1
        entities.append(dict(id=f'e{i}',message_id=m,mention=q,type=t,person_id=p,occurrence=occurrence))
    doc=materialize_quotes({**source,'schema_version':'annotation-quotes-v1',
        'case_flags':selection['case_flags'],'entities':entities},source)
    result=assess(doc)
    doc['case_flags']=result['case_flags']
    actual=result['first_alert']
    signature=[actual['message_id'],actual['reason']] if actual else None
    if signature!=selection['expected_first_alert']:
        raise ValueError('Авторское ожидание разошлось с обработчиком')
    return {'annotation':doc,'assessment':result,'notes':selection['notes']}


def freeze(dataset,run):
    if run.exists():
        raise ValueError('Слепая разметка должна быть зафиксирована до API-прогона')
    source={s['session_id']:s for s in read(dataset/'sessions.json')['sessions']}
    selection_path=dataset/'blind-review-selections.json'
    selected=read(selection_path)
    plan=read(dataset/'review-plan.json')
    if {s['session_id'] for s in selected['sessions']}!=set(plan['blind_session_ids']):
        raise ValueError('Слепая выборка отличается от заранее заданного плана')
    rows=[materialize(source[s['session_id']],s) for s in selected['sessions']]
    write(dataset/'blind-review-frozen.json',{'frozen_at':datetime.now(timezone.utc).isoformat(),
        'model_outputs_viewed':False,'reviewer':'current_interactive_llm_not_independent_human',
        'input_sha256':hashlib.sha256((dataset/'sessions.json').read_bytes()).hexdigest(),
        'selection_sha256':hashlib.sha256(selection_path.read_bytes()).hexdigest(),'reviews':rows})
    print(json.dumps({'blind_reviews_frozen':len(rows),'api_run_not_started':True}))


def report(dataset,run):
    sessions={s['session_id']:s for s in read(dataset/'sessions.json')['sessions']}
    manifest=read(run/'manifest.json')
    if any(r['status']=='requested' for r in manifest['records']):
        raise ValueError('Прогон ещё выполняется')
    if manifest['input_sha256']!=hashlib.sha256((dataset/'sessions.json').read_bytes()).hexdigest():
        raise ValueError('Другой исходный набор')
    blind=read(dataset/'blind-review-frozen.json')
    if blind['frozen_at']>=manifest['started_at']:
        raise ValueError('Слепая проверка зафиксирована после начала API-прогона')
    visible=read(dataset/'visible-review-selections.json')
    plan=read(dataset/'review-plan.json')
    if {s['session_id'] for s in visible['sessions']}!=set(plan['label_visible_session_ids']):
        raise ValueError('Выборка с метками отличается от плана')
    references={r['annotation']['session_id']:('blind',r) for r in blind['reviews']}
    references.update({s['session_id']:('label_visible',materialize(sessions[s['session_id']],s)) for s in visible['sessions']})
    stratum_sample=read(dataset/'stratum-review-sample.json')
    stratum_verdicts=read(dataset/'stratum-review-verdicts.json')
    ban_verdicts=read(dataset/'ban-review-verdicts.json')
    sampled={(row['session_id'],row['message_id'],group) for group,rows in stratum_sample['sample'].items() for row in rows}
    checked={(r['session_id'],r['message_id'],r['stratum']) for r in stratum_verdicts['reviews']}
    if sampled!=checked or len(checked)!=len(stratum_verdicts['reviews']):
        raise ValueError('Проверенные сообщения не совпали со случайной стратифицированной выборкой')
    counts,types=Counter(),Counter()
    counts.update({key:0 for key in ('complete_sessions','incomplete_sessions','ban_sessions','no_ban_sessions',
        'uncertain_complete_sessions','uncertain_no_ban_sessions','unknown_owner_entities','entities',
        'model_reported_uncertainty_complete_sessions')})
    cases,reviews,failures=[],[],[]
    lines=['# Русские сессии: данные, Gemma и выборочная перепроверка','','Под «баном» здесь понимается вычисленный алерт; фактических блокировок нет.','']
    annotated=[]
    for number,record in enumerate(manifest['records'],1):
        sid=record['session_id']
        case={'session_id':sid,'status':record['status'],'completed_messages':record['completed_messages']}
        if record['status']!='valid':
            counts['incomplete_sessions']+=1
            case.update(error_type=record.get('error_type'),http_status=record.get('http_status'))
            failed_length=record['completed_messages']+1
            stem=f'{number:02d}-m{failed_length:02d}'
            raw_path=run/f'{stem}-model-output.json'
            failure={'session_id':sid,'message_id':f'm{failed_length}',
                'error_type':record.get('error_type'),'http_status':record.get('http_status'),
                'review_mode':'targeted_failure_diagnosis_not_random_review'}
            if raw_path.exists():
                raw=read(raw_path)
                payload=json.loads(read(run/f'{stem}-request.json')['messages'][1]['content'])
                texts={m['id']:m['text'] for m in payload['session']['messages']}
                failure['quote_occurrences']=[{'mention':e.get('mention'),'occurrence':e.get('occurrence'),
                    'exact_count_in_message':texts.get(e.get('message_id'),'').count(e['mention']) if isinstance(e.get('mention'),str) and e['mention'] else None}
                    for e in raw.get('entities',[]) if isinstance(e,dict)]
                try:
                    merge_step(raw,payload)
                except (ValueError,KeyError,TypeError,IndexError) as error:
                    failure['validation_error']=str(error)
            failures.append(failure)
            cases.append(case)
            continue
        pred=read(run/f'{number:02d}-annotation.json')
        result=read(run/f'{number:02d}-assessment.json')
        raw_flags=read(run/f'{number:02d}-m{len(pred["messages"]):02d}-model-output.json')['case_flags']
        counts['complete_sessions']+=1
        counts['ban_sessions' if result['session_alert'] else 'no_ban_sessions']+=1
        counts['uncertain_complete_sessions']+=result['case_flags']['annotation_uncertain']
        counts['model_reported_uncertainty_complete_sessions']+=raw_flags['annotation_uncertain']
        if not result['session_alert']:
            counts['uncertain_no_ban_sessions']+=result['case_flags']['annotation_uncertain']
        counts['unknown_owner_entities']+=sum(e['person_id'] is None for e in pred['entities'])
        counts['entities']+=len(pred['entities'])
        types.update(e['type'] for e in pred['entities'])
        case.update(first_alert=result['first_alert'],flags=result['case_flags'],raw_model_flags=raw_flags,entities=len(pred['entities']))
        annotated += [f'## {sid}', '', f"Алерт: `{result['first_alert']}`; флаги: `{result['case_flags']}`.",'']
        annotated += [f"**{m['id']}** {m['text']}\n" for m in pred['messages']]
        annotated += ['| Сообщение | Фрагмент | Тип | Владелец |','|---|---|---|---|']
        texts={m['id']:m['text'] for m in pred['messages']}
        annotated += [f"| {e['message_id']} | {texts[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |" for e in pred['entities']]
        annotated += ['']
        if sid in references:
            mode,review=references[sid]
            ref=review['annotation']
            g,p=set(map(key,ref['entities'])),set(map(key,pred['entities']))
            comparison={'session_id':sid,'mode':mode,'extraction':score(len(g&p),len(p-g),len(g-p)),
                'all_spans_and_ownership_exact':groups(ref)==groups(pred),
                'reference_first_alert':review['assessment']['first_alert'],'model_first_alert':result['first_alert'],
                'missing_spans':[list(v) for v in sorted(g-p)],'extra_spans':[list(v) for v in sorted(p-g)],'notes':review['notes'],
                'reference_flags':review['assessment']['case_flags'],'model_flags':result['case_flags']}
            for name,values in (('missing_mentions',g-p),('extra_mentions',p-g)):
                comparison[name]=[{'message_id':m,'mention':texts[m][start:end],'type':kind}
                                  for m,start,end,kind in sorted(values)]
            def owner_view(document):
                values={}
                for e in document['entities']:
                    values.setdefault(str(e['person_id']),[]).append({'message_id':e['message_id'],
                        'mention':texts[e['message_id']][e['start']:e['end']],'type':e['type']})
                return values
            comparison.update(reference_owners=owner_view(ref),model_owners=owner_view(pred))
            signature=lambda a:[a['message_id'],a['reason']] if a else None
            comparison['first_alert_matches']=signature(comparison['reference_first_alert'])==signature(comparison['model_first_alert'])
            reviews.append(comparison)
        cases.append(case)
    write(dataset/'reviewed-references.json',{'references':[{**r,'mode':mode} for mode,r in references.values()],
        'not_all_sessions_have_gold':True,'human_review_pending':True})
    verification=audit(dataset,run)
    write(dataset/'sequential-audit.json',verification)
    reviewed_messages={(r['session_id'],m['id']) for r in reviews for m in sessions[r['session_id']]['messages']}
    reviewed_messages.update((r['session_id'],r['message_id']) for r in stratum_verdicts['reviews'])
    reviewed_messages.update((r['session_id'],m) for r in ban_verdicts['reviews'] for m in r['messages_reviewed'])
    bans={c['session_id']:c['first_alert'] for c in cases if c['status']=='valid' and c['first_alert']}
    if set(bans)!={r['session_id'] for r in ban_verdicts['reviews']}:
        raise ValueError('Перепроверены не все положительные сессии')
    for r in ban_verdicts['reviews']:
        actual=bans[r['session_id']]
        if [actual['message_id'],actual['reason']]!=r['expected_first_alert']:
            raise ValueError('Вердикт проверки алерта отличается от сохранённого момента')
    summary={'source':read(dataset/'manifest.json'),'model':manifest['requested_model'],
        'blind_review_frozen_at':blind['frozen_at'],'model_run_started_at':manifest['started_at'],
        'selected_sessions':len(sessions),'api_requests':manifest['api_requests'],
        'valid_steps':sum(s['status']=='valid' for s in manifest['steps']),
        'unattempted_session_ids':sorted(set(sessions)-{r['session_id'] for r in manifest['records']}),
        'counts':dict(counts),'entity_types':dict(sorted(types.items())),'cases':cases,'reviews':reviews,
        'targeted_failures':failures,
        'review_coverage':{'blind_planned':plan['blind_session_ids'],'label_visible_planned':plan['label_visible_session_ids'],
                           'completed_reviews':len(reviews),'unique_messages_reviewed':len(reviewed_messages),
                           'stratum_message_reviews':len(stratum_verdicts['reviews']),
                           'planned_sessions_without_complete_prediction':sorted(set(references)-{r['session_id'] for r in reviews})},
        'stratum_review':{'eligible_counts':stratum_sample['eligible_counts'],'verdicts':stratum_verdicts['reviews']},
        'targeted_ban_reviews':ban_verdicts['reviews'],
        'adjudications':read(dataset/'review-adjudications.json'),
        'limitations':['Короткие отобранные пользовательские префиксы, а не полные исходные сессии',
            'Перепроверка текущей LLM, а не независимый человеческий эталон',
            'Проверка с видимыми метками подвержена эффекту привязки к ответу модели',
            'Разговорные имена подтверждены пользователем; исторические сомнения сохранены. Литературные цитаты остаются вопросом ТЗ',
            'По частичной проверенной подвыборке нельзя заявлять точность на всём корпусе']}
    write(dataset/'overview.json',summary)
    lines += ['## Объём и предсказания','', '```json',json.dumps({k:summary[k] for k in ('selected_sessions','api_requests','valid_steps','counts','entity_types')},ensure_ascii=False,indent=2),'```','',
        '| Сессия | Статус | Алерт | Сущностей | Неуверенность |','|---|---|---|---|---|']
    for c in cases:
        alert=c.get('first_alert')
        lines += [f"| {c['session_id']} | {c['status']} | {alert['message_id']+'/'+alert['reason'] if alert else 'нет' if c['status']=='valid' else 'не оценено'} | {c.get('entities','—')} | {c.get('flags',{}).get('annotation_uncertain','—')} |"]
    lines += ['', '## Перепроверка', '', 'Слепые решения зафиксированы до API-прогона; источник исходных NER-меток не использовался. Проверка с метками проведена после прогона. Обе проверки выполнены текущей LLM, не независимым человеком.','']
    for r in reviews:
        lines += [f"### {r['session_id']} — {r['mode']}",'',
            f"Спаны: `{r['extraction']}`; полное совпадение спанов/владельцев: `{r['all_spans_and_ownership_exact']}`.",
            f"Алерт проверки: `{r['reference_first_alert']}`; алерт по Gemma: `{r['model_first_alert']}`.",'']
        if r['missing_mentions']:
            lines += ['Пропуски: `'+json.dumps(r['missing_mentions'],ensure_ascii=False)+'`.','']
        if r['extra_mentions']:
            lines += ['Лишние спаны: `'+json.dumps(r['extra_mentions'],ensure_ascii=False)+'`.','']
        if not r['all_spans_and_ownership_exact']:
            lines += ['Группы проверки: `'+json.dumps(r['reference_owners'],ensure_ascii=False)+'`.',
                      'Группы Gemma: `'+json.dumps(r['model_owners'],ensure_ascii=False)+'`.','']
        lines += ['Флаги проверки: `'+json.dumps(r['reference_flags'],ensure_ascii=False)+'`; флаги после Gemma/обработчика: `'+json.dumps(r['model_flags'],ensure_ascii=False)+'`.','']
        lines += [f'- {note}' for note in r['notes']]
        lines += ['']
    lines += ['## Случайные сообщения без меток и с метками Gemma','',
              'Подвыборка сформирована по правилу и seed, заданным во время запуска. Проверялись текущие сообщения с доступным предыдущим пользовательским контекстом.','']
    for row in stratum_verdicts['reviews']:
        lines += [f"### {row['session_id']} / {row['message_id']} — {row['stratum']}",'',
                  f"Вердикт проверки: **{row['verdict']}**.",row['notes'],'']
    if failures:
        lines += ['## Дополнительный целевой разбор невалидных ответов','',
                  'Эта проверка вызвана ошибкой, не входит в случайную подвыборку. Ответы не исправлялись.','',
                  '```json',json.dumps(failures,ensure_ascii=False,indent=2),'```','']
    lines += ['## Целевая проверка всех алертов','', 'Это дополнительная проверка положительных случаев, не случайная оценка качества.','']
    for r in ban_verdicts['reviews']:
        lines += [f"### {r['session_id']} — {r['verdict']}",'',r['notes'],'']
    lines += ['## Уточнения пользователя','']+[d['decision'] for d in summary['adjudications']['decisions']]+['']
    lines += ['## Ограничения','']+[f'- {note}' for note in summary['limitations']]
    write_text(dataset/'overview.md','\n'.join(lines))
    write_text(dataset/'gemma-annotations-for-review.md','# Сущности Gemma и расчёт обработчика\n\nСущности и владельцы не исправлялись. Показанные алерты и накопленные флаги рассчитаны обработчиком; сырые ответы сохранены отдельно в каталоге прогона.\n\n'+'\n'.join(annotated))
    print(json.dumps({k:summary[k] for k in ('selected_sessions','api_requests','valid_steps','counts','entity_types','review_coverage')},ensure_ascii=False,indent=2))


def main():
    global VERIFY,REFRESH
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=DEFAULT)
    parser.add_argument('--run',type=Path,default=RUN)
    parser.add_argument('--freeze-blind',action='store_true')
    parser.add_argument('--sample-strata',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--refresh-derived',action='store_true',help='Пересобрать только производные отчёты, не исходные данные, выборки, слепую разметку или ответы модели')
    args=parser.parse_args()
    VERIFY=args.check
    REFRESH=args.refresh_derived
    if args.refresh_derived and (args.check or args.freeze_blind or args.sample_strata):
        parser.error('--refresh-derived применим только к сборке отчёта')
    if args.freeze_blind and (args.check or args.sample_strata):
        parser.error('Слепая проверка фиксируется отдельной командой без --check')
    if args.freeze_blind:
        freeze(args.dataset,args.run)
    elif args.sample_strata:
        strata(args.dataset,args.run)
    else:
        report(args.dataset,args.run)


if __name__=='__main__':
    main()
