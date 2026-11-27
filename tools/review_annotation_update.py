"""Новый снимок двух разметчиков и разбор прироста после прежнего аудита Gemma."""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path

from review_gemma_completed import capture,analyse,signature

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def calculate(gemma,qwen,previous):
    old_ids={r['session_id'] for r in previous['records']}
    if not old_ids<={r['session_id'] for r in gemma['records']}:
        raise ValueError('Новый снимок потерял ранее завершённые сессии')
    delta={**gemma,'records':[r for r in gemma['records'] if r['session_id'] not in old_ids]}
    reports={'gemma_all':analyse(gemma),'gemma_new':analyse(delta),'qwen_all':analyse(qwen)}
    for item in reports['qwen_all']['details']:
        item['annotations']['qwen']=item['annotations'].pop('gemma')
    return reports


def markdown(reports):
    lines=['# Проверка новых ответов разметчиков','',
           'Сравнение с авторской синтетической разметкой; проверка человеком ожидается. '
           'Завершённые ответы считаются по уникальным сессиям, ошибки сети не становятся отрицательными примерами.','',
           '| Показатель | Gemma: всего | Gemma: новые | Qwen: всего |','|---|---:|---:|---:|']
    for title,key in [('Завершённые сессии','completed_sessions'),('Точные спаны и владельцы','exact_spans_and_owners'),
                      ('Совпадение первого алерта','exact_first_alert'),('Ошибки владельцев на общих границах','ownership_disagreement_on_common_spans')]:
        values=[r[key] if key=='completed_sessions' else r['counts'].get(key,0) for r in reports.values()]
        lines.append('| '+title+' | '+' | '.join(map(str,values))+' |')
    for name,report in reports.items():
        lines += ['',f'## {name}','',f"Сессионные исходы: {report['session']}.",
                  f"Семейства разногласий: {report['mismatch_families']}.",
                  f"После офлайн-фильтра checksum: {report['counts'].get('offline_checksum_filter_false_alerts',0)} ложных алертов; "
                  f"первый алерт совпал в {report['counts'].get('offline_checksum_filter_first_alert_exact',0)} сессиях."]
        for item in report['details']:
            lines += ['',f"### {item['session_id']} — {item['family']}",'']
            lines += [f"**{m['id']}:** {m['text']}" for m in item['messages']]
            lines += ['', '| Источник | Сообщение | Фрагмент | Тип | Владельцы |','|---|---|---|---|---|']
            for label,entities in item['annotations'].items():
                lines += [f"| {label} | {e['message_id']} | {e['text']} | {e['type']} | {e['person_id']} |" for e in entities]
            case=item['comparison']
            lines += ['',f"Алерт автора: `{case['gold_first_alert']}`. Алерт обработчика: `{case['predicted_first_alert']}`.",
                      f"Checksum: `{item['checksum_issues']}`. Владельцы на общих границах совпадают: `{item['common_span_ownership_matches']}`."]
    lines += ['', 'Офлайн-фильтр не является новым вызовом модели. '
              'Изменения активной инструкции, исходных gold и сохранённых ответов этим аудитом не выполняются.']
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--gemma-gold',type=Path)
    parser.add_argument('--qwen-gold',type=Path)
    parser.add_argument('--previous',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    target=args.output.resolve()
    if not target.is_relative_to(ROOT/'data'):
        parser.error('Результаты должны находиться внутри data/')
    if args.check:
        gemma,qwen,previous=[read(target/name) for name in ['gemma-snapshot.json','qwen-snapshot.json','previous-snapshot.json']]
    else:
        if target.exists() or any(v is None for v in [args.state,args.gemma_gold,args.qwen_gold,args.previous]):
            parser.error('Нужны источники и новый выходной каталог')
        gemma=capture(args.state,args.gemma_gold)
        qwen=capture(args.state,args.qwen_gold,'second_runs','Qwen3.8-27B')
        previous=read(args.previous/'snapshot.json')
    reports=calculate(gemma,qwen,previous)
    if args.check:
        if json.loads(json.dumps(reports))!=read(target/'review.json') or markdown(reports)!=(target/'review.md').read_text(encoding='utf-8'):
            raise ValueError('Результат отличается от снимка')
    else:
        target.mkdir(parents=True)
        for name,value in [('gemma-snapshot.json',gemma),('qwen-snapshot.json',qwen),('previous-snapshot.json',previous),('review.json',reports)]:
            (target/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (target/'review.md').write_text(markdown(reports),encoding='utf-8')
    print(json.dumps({name:{k:v for k,v in report.items() if k!='details'} for name,report in reports.items()},ensure_ascii=False))
