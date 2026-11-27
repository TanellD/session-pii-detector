"""Отчёт завершённого последовательного прогона: эталон, семейства, ошибки и время."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics
import time

from audit_sequential_annotation import audit
from compare_annotation_models import compare

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data/experiments/annotation-100-2026-11-19'
RUN = ROOT/'data/experiments/radeon/2026-11-19-annotation-100-gemma-02'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def report(source,run):
    manifest = read(run/'manifest.json')
    if 'finished_at' not in manifest:
        raise ValueError('Прогон ещё не завершён; нельзя выдавать промежуточные итоги за полные')
    comparison = compare(source,run)
    protocol = audit(source,run)
    uncertain = Counter()
    for n,record in enumerate(manifest['records'],1):
        if record['status']=='valid':
            assessment = read(run/f'{n:02d}-assessment.json')
            flags = assessment['case_flags']
            if flags['annotation_uncertain']:
                uncertain['sessions'] += 1
                uncertain['with_alert' if assessment['session_alert'] else 'without_alert'] += 1
                uncertain['reason_'+str(flags['clarification_reason'])] += 1
    checks = {x['session_id']:x for x in read(source/'gold-assessments.json')['checks']}
    families = defaultdict(Counter)
    for case in comparison['cases']:
        row = families[checks[case['session_id']]['family']]
        row['attempted'] += 1
        row[case['status']] += 1
        if case['status']=='valid':
            row['exact_spans_and_owners'] += case['all_spans_and_ownership_exact']
            signature = lambda f: (f['message_id'],f['reason']) if f else None
            row['exact_first_alert'] += signature(case['gold_first_alert'])==signature(case['predicted_first_alert'])
            row['raw_flags_exact'] += case['model_flags_exact']
            row['processed_flags_exact'] += case['processed_flags_exact']
    times = [s['request_seconds'] for s in manifest['steps'] if 'request_seconds' in s]
    timing = dict(requests=len(times),mean_seconds=statistics.mean(times),median_seconds=statistics.median(times),
                  p95_nearest_rank_seconds=sorted(times)[math.ceil(.95*len(times))-1],max_seconds=max(times)) if times else {}
    errors = []
    for number, record in enumerate(manifest['records'],1):
        if record['status']=='valid':
            continue
        steps = [s for s in manifest['steps'] if s['session_id']==record['session_id']]
        last = steps[-1]
        raw = run/f'{number:02d}-m{len(steps):02d}-model-output.json'
        errors.append(dict(session_id=record['session_id'],message_id=last['message_id'],
                           error_type=record.get('error_type'),http_status=record.get('http_status'),
                           model_output_saved=raw.exists()))
    return dict(comparison=comparison,protocol=protocol,uncertainty=dict(uncertain),
                fp0_scope_note='comparison.fp0 uses the confident-negative subset in metrics section 4; all valid sessions including uncertain ones enter session/timely scores',
                families={k:dict(v) for k,v in sorted(families.items())},
                timing=timing,failures=errors,started_at=manifest['started_at'],finished_at=manifest['finished_at'])


def review_markdown(source,run,result):
    gold = {d['session_id']:d for d in read(source/'gold.json')['annotations']}
    cases = {c['session_id']:c for c in result['comparison']['cases']}
    records = read(run/'manifest.json')['records']
    lines = ['# Разметка Gemma и авторский эталон: 100 синтетических сессий', '',
             'Эталон подготовлен текущей LLM до API-запуска. Человеческая проверка ожидается.',
             'Это 20 семейств по 5 вариантов, а не 100 независимых естественных диалогов.', '']
    for n,record in enumerate(records,1):
        doc = gold[record['session_id']]
        case = cases[record['session_id']]
        lines += [f"## {record['session_id']} — {record['status']}", '']
        lines += [f"**{m['id']}:** {m['text']}\n" for m in doc['messages']]
        if record['status']!='valid':
            lines += [f"Сессия не завершена: {record.get('error_type')}. Это не отрицательная метка.", '']
            continue
        pred = read(run/f'{n:02d}-annotation.json')
        lines += [f"Эталонный алерт: `{case['gold_first_alert']}`.",
                  f"Предсказанный алерт: `{case['predicted_first_alert']}`.",
                  f"Флаги эталона: `{doc['case_flags']}`; флаги модели: `{case['model_flags']}`.", '',
                  '| Источник | Сообщение | Фрагмент | Тип | Персона |', '|---|---|---|---|---|']
        for label,annotation in [('Эталон',doc),('Gemma',pred)]:
            texts = {m['id']:m['text'] for m in annotation['messages']}
            lines += [f"| {label} | {e['message_id']} | {texts[e['message_id']][e['start']:e['end']]} | {e['type']} | {e['person_id']} |"
                      for e in annotation['entities']]
        lines += ['']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--run',type=Path,default=RUN)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--refresh-derived',action='store_true',help='Заменить только три производных файла отчёта; исходники и ответы не менять')
    parser.add_argument('--wait',action='store_true',help='Дождаться finished_at и сохранить итог без нового API-запуска')
    args = parser.parse_args()
    if args.wait:
        deadline = time.monotonic()+10800
        while True:
            try:
                finished = 'finished_at' in read(args.run/'manifest.json')
            except json.JSONDecodeError:
                finished = False  # Манифест мог читаться во время записи.
            if finished:
                break
            if time.monotonic()>=deadline:
                raise TimeoutError('За время ожидания прогон не завершился; итог не сохранён')
            time.sleep(5)
    result = report(args.source,args.run)
    artifacts = {'overview.json':json.dumps(result,ensure_ascii=False,indent=2)+'\n',
                 'gemma-annotations-for-review.md':review_markdown(args.source,args.run,result)}
    summary = {k:v for k,v in result['comparison'].items() if k not in ('cases','offsets_messages')}
    c = result['comparison']
    lines = ['# Итог прогона Gemma на 100 синтетических сессиях', '',
             'Метрики рассчитаны только на завершённых валидных сессиях. Ошибки и покрытие указаны отдельно.',
             '20 семейств по 5 вариантов; авторский LLM-эталон, независимой человеческой оценки нет.', '',
             'Воспроизведение: `python -X utf8 tools/report_annotation_100.py --check`.', '',
             f"Завершено: **{c['valid_sessions']} из {c['total_gold_sessions']}** сессий.", '',
             f"Среди завершённых: **{c['session']['tp']+c['session']['fp']} с алертом**, **{c['session']['tn']+c['session']['fn']} без алерта**.", '',
             f"Спаны: совпало **{c['extraction']['tp']}**, пропущено **{c['extraction']['fn']}**, лишних **{c['extraction']['fp']}**.", '',
             f"Все спаны и их группировка по людям совпали в **{c['all_spans_and_ownership_exact_sessions']}** сессиях.", '',
             f"Сохраняемая пометка недостатка статистики: **{result['uncertainty'].get('sessions',0)}** сессий; из них **{result['uncertainty'].get('without_alert',0)}** без алерта. Эти сессии включены в F2-session и F2-timely.", '',
             'Отсутствие алерта означает отсутствие достаточного основания по текущей политике, а не отсутствие персональных данных. Флаг неуверенности может сохраняться и после позднего алерта.', '',
             'Поле `comparison.fp0` показывает только подмножество уверенно отрицательных сессий по разделу 4 протокола; его знаменатель отличается от общего числа отрицательных сессий. Остальные сессионные метрики включают все валидные сессии.', '',
             f"Медиана запроса: **{result['timing']['median_seconds']:.2f} с**; среднее: **{result['timing']['mean_seconds']:.2f} с**.", '',
             'Полные тексты, эталон и предсказания: [gemma-annotations-for-review.md](gemma-annotations-for-review.md).', '',
             'Все расхождения и аудит запросов находятся в [overview.json](overview.json).', '',
             '## Сравнение', '', '```json', json.dumps(summary,ensure_ascii=False,indent=2), '```', '',
             '## Семейства', '', '```json',json.dumps(result['families'],ensure_ascii=False,indent=2),'```', '',
             '## Время запросов', '', '```json',json.dumps(result['timing'],ensure_ascii=False,indent=2),'```']
    artifacts['overview.md']='\n'.join(lines)+'\n'
    for filename,content in artifacts.items():
        path = args.source/filename
        if args.check:
            if path.read_text(encoding='utf-8')!=content:
                raise ValueError(f'Отчёт изменился: {filename}')
        elif path.exists() and not args.refresh_derived:
            raise ValueError(f'Не перезаписываем отчёт: {filename}')
    if not args.check:
        for filename,content in artifacts.items():
            (args.source/filename).write_text(content,encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('families','timing','failures')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
