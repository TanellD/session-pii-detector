"""Зафиксировать и проверить завершённые ответы Gemma без изменения активных прогонов."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from annotation_contract import assess
from annotation_calculator import Calculator
from compare_annotation_models import compare, groups, score
from run_annotation_batch import CHECKS, checksum_issues

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def signature(first):
    return (first['message_id'],first['reason']) if first else None


def capture(state_path,gold_dir,run_group='first_runs',model='Gemma-4-31B-IT'):
    source=read(gold_dir/'sessions.json')
    if source.get('synthetic_only') is not True:
        raise ValueError('Нужен явно синтетический корпус')
    gold={d['session_id']:d for d in read(gold_dir/'gold.json')['annotations']}
    checks={c['session_id']:c for c in read(gold_dir/'gold-assessments.json')['checks']}
    selected={}
    failures=Counter()
    for root in read(state_path)[run_group]:
        for job in read(Path(root)/'batch-manifest.json')['jobs']:
            run=Path(job['run'])
            if not (run/'manifest.json').exists():
                continue
            manifest=read(run/'manifest.json')
            if manifest['requested_model']!=model:
                raise ValueError('В выбранной группе обнаружена другая модель')
            failures.update(s.get('error_type') or str(s.get('http_status'))
                            for s in manifest['steps'] if s['status'] not in {'valid','requested'})
            comparison=compare(Path(job['source']),run)
            by_case={c['session_id']:c for c in comparison['cases']}
            for number,record in enumerate(manifest['records'],1):
                sid=record['session_id']
                if record['status']!='valid' or sid in selected:
                    continue
                pred_path=run/f'{number:02d}-annotation.json'
                request=read(run/f'{number:02d}-m01-request.json')
                instructions=request['messages'][0]['content']
                allowed=next((line for line in instructions.splitlines() if line.startswith('Разрешённые типы:')),'')
                types=request['response_format']['json_schema']['schema']['properties']['entities']['items']['properties']['type']['enum']
                selected[sid]=dict(session_id=sid,family=checks[sid]['family'],gold=gold[sid],
                    author_assessment=checks[sid]['assessment'],prediction=read(pred_path),
                    assessment=read(run/f'{number:02d}-assessment.json'),comparison=by_case[sid],
                    prediction_path=str(pred_path),prediction_sha256=hashlib.sha256(pred_path.read_bytes()).hexdigest(),
                    prompt_sha256=manifest['prompt_sha256'],allowed_types_line=allowed,
                    person_absent_from_explicit_list='person' not in re.findall(r'`([^`]+)`',allowed),
                    person_present_in_transport_schema='person' in types)
    return dict(captured_at=datetime.now(timezone.utc).isoformat(),
        model=model,total_source_sessions=len(gold),records=list(selected.values()),
        transport_failure_steps=dict(failures),gold_sha256=hashlib.sha256((gold_dir/'gold.json').read_bytes()).hexdigest(),
        policy_sha256=hashlib.sha256((ROOT/'configs/annotation-estimator-policy.json').read_bytes()).hexdigest(),
        source_status='assistant_authored_synthetic_reference; human_review_pending',
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def common_span_owners_equal(gold,prediction):
    def position(entity):
        return entity['message_id'],entity['start'],entity['end']
    common={position(e) for e in gold['entities']} & {position(e) for e in prediction['entities']}
    def project(doc):
        return {**doc,'entities':[{**e,'type':'attribute'} for e in doc['entities'] if position(e) in common]}
    return groups(project(gold))==groups(project(prediction))


def analyse(snapshot):
    calculator=Calculator()
    totals=Counter()
    families=Counter()
    extraction=Counter()
    outcomes=Counter()
    timely=Counter()
    details=[]
    for item in snapshot['records']:
        gold,prediction,case=item['gold'],item['prediction'],item['comparison']
        counts=case['entity_counts']
        extraction.update(tp=counts['matched'],fp=counts['extra'],fn=counts['missed'])
        outcomes[case['session_outcome']]+=1
        timely[case['timely_outcome']]+=1
        totals['exact_spans_and_owners']+=case['all_spans_and_ownership_exact']
        totals['exact_first_alert']+=signature(item['author_assessment']['first_alert'])==signature(item['assessment']['first_alert'])
        totals['prompt_person_list_omission']+=item['person_absent_from_explicit_list']
        totals['schema_supports_person']+=item['person_present_in_transport_schema']
        totals['ownership_disagreement_on_common_spans']+=not common_span_owners_equal(gold,prediction)
        invalid=checksum_issues(prediction)
        filtered=copy.deepcopy(prediction)
        invalid_ids={e['entity_id'] for e in invalid}
        filtered['entities']=[e for e in filtered['entities'] if e['id'] not in invalid_ids]
        filtered_assessment=assess(filtered,calculator)
        filtered_first=signature(filtered_assessment['first_alert'])
        expected_first=signature(item['author_assessment']['first_alert'])
        totals['offline_checksum_filter_first_alert_exact']+=filtered_first==expected_first
        totals['offline_checksum_filter_false_alerts']+=expected_first is None and filtered_first is not None
        totals['sessions_with_invalid_checksum_spans']+=bool(invalid)
        totals['false_alert_sessions_with_invalid_checksum']+=bool(invalid) and case['session_outcome']=='fp'
        if not case['all_spans_and_ownership_exact']:
            families[item['family']]+=1
        positions={}
        for kind,doc in [('author',gold),('gemma',prediction)]:
            texts={m['id']:m['text'] for m in doc['messages']}
            positions[kind]=[dict(message_id=e['message_id'],text=texts[e['message_id']][e['start']:e['end']],
                                  type=e['type'],person_id=e['person_id']) for e in doc['entities']]
        if not case['all_spans_and_ownership_exact'] or signature(item['assessment']['first_alert'])!=expected_first:
            details.append(dict(session_id=item['session_id'],family=item['family'],messages=gold['messages'],
                annotations=positions,comparison=case,checksum_issues=invalid,
                offline_filtered_first_alert=filtered_first,
                common_span_ownership_matches=common_span_owners_equal(gold,prediction)))
    total=len(snapshot['records'])
    return dict(captured_at=snapshot['captured_at'],completed_sessions=total,total_source_sessions=snapshot['total_source_sessions'],
        scope='completed_unique_sessions_only; not final quality on entire corpus; author reference unverified',
        counts=dict(totals),mismatch_families=dict(families),
        extraction=score(extraction['tp'],extraction['fp'],extraction['fn']),
        session={**score(outcomes['tp'],outcomes['fp'],outcomes['fn']),'tn':outcomes['tn']},
        timely={**score(timely['tp'],timely['fp'],timely['fn']),'tn':timely['tn']},
        details=details,
        checksum_counterfactual='offline recalculation on same saved final predictions; not a new model run or proof of unchanged future predictions')


def markdown(report):
    counts=report['counts']
    lines=['# Проверка завершённых аннотаций Gemma','',f"Снимок: {report['captured_at']}.",
        f"Проверены {report['completed_sessions']} завершённых уникальных сессий из {report['total_source_sessions']}.",
        'Это сравнение с авторской синтетической разметкой, не подтверждённой человеком. Ошибки транспорта не считаются отрицательными примерами.','',
        f"Точные спаны и группировка владельцев совпали: {counts['exact_spans_and_owners']}.",
        f"Первый алерт совпал: {counts['exact_first_alert']}.",
        f"Сессионные исходы: {report['session']}; временные: {report['timely']}.",
        f"Разногласия по семействам: {report['mismatch_families']}.",'',
        '## Подтверждённый пробел обработчика checksum','',
        f"Невалидные документные спаны найдены в {counts['sessions_with_invalid_checksum_spans']} сессиях; "
        f"в {counts['false_alert_sessions_with_invalid_checksum']} они привели к ложному красному алерту обработчика.",
        'Инструкция запрещает LLM самостоятельно считать checksum и обещает проверку валидатором. '
        'Но merge_step вызывает materialize_quotes → validate → assess; validate проверяет структуру, а не checksum. '
        'Строгий validate_review_document используется при подготовке корпуса, но не на этом пути ответа модели.',
        f"Офлайн-проверка тех же сохранённых ответов после удаления невалидных документных спанов даёт "
        f"{counts['offline_checksum_filter_false_alerts']} ложных алертов и {counts['offline_checksum_filter_first_alert_exact']} совпадений первого алерта.",
        'Это диагностический пересчёт, а не повторная модельная разметка: изменение прежнего состояния в новом запуске может изменить дальнейшие ответы.','',
        '## Противоречие инструкции PERSON','',
        f"В {counts['prompt_person_list_omission']} проверенных сессиях person отсутствует в явном списке разрешённых типов, "
        f"при том что схема поддерживает его в {counts['schema_supports_person']} сессиях и поздний абзац разрешает общий PERSON.",
        'Пропуск псевдонима нельзя уверенно объявить чистой ошибкой модели: инструкция противоречива, '
        'а включение псевдонимов в границы PERSON требует явного примера/уточнения.','',
        '## Тип вымышленного имени','',
        'Повторяется разногласие о слове «Шаблоний»: автор указал first_name, Gemma — last_name. '
        'Фразы «мой сосед — Шаблоний» или «коллега — Шаблоний» не всегда однозначно задают роль части. '
        'Это кандидат на уточнение авторского сценария, а не доказательство ошибки модели во всех таких случаях.',
        f"Разногласий по группировке владельцев на общих границах: {counts['ownership_disagreement_on_common_spans']}. "
        'Это ограниченная проверка: она не измеряет владельцев пропущенных спанов и не доказывает качество реального сервисного линкера.','',
        '## Разбор сессий','']
    for item in report['details']:
        lines += [f"### {item['session_id']} — {item['family']}",'']
        lines += [f"**{m['id']}:** {m['text']}" for m in item['messages']]
        lines += ['', '| Источник | Сообщение | Фрагмент | Тип | Владельцы |','|---|---|---|---|---|']
        for label,entities in item['annotations'].items():
            lines += [f"| {label} | {e['message_id']} | {e['text']} | {e['type']} | {e['person_id']} |" for e in entities]
        case=item['comparison']
        lines += ['',f"Первый алерт: автор `{case['gold_first_alert']}`; обработчик ответа Gemma `{case['predicted_first_alert']}`.",'']
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--gold',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    target=args.output.resolve()
    if not target.is_relative_to(ROOT/'data'):
        parser.error('Результаты разрешены только внутри data/')
    if args.check:
        snapshot=read(target/'snapshot.json')
        result=analyse(snapshot)
        canonical=json.loads(json.dumps(result,ensure_ascii=False))
        if canonical!=read(target/'review.json') or markdown(result)!=(target/'review.md').read_text(encoding='utf-8'):
            raise ValueError('Сохранённый разбор отличается от пересчёта снимка')
    else:
        if target.exists() or not args.state or not args.gold:
            parser.error('Нужны --state, --gold и новый каталог --output')
        snapshot=capture(args.state,args.gold)
        result=analyse(snapshot)
        target.mkdir(parents=True)
        for name,value in [('snapshot.json',snapshot),('review.json',result)]:
            (target/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (target/'review.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False))
