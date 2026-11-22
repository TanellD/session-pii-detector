"""Сравнить сохранённые ответы моделей с авторским эталоном без API-запросов."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def score(tp, fp, fn):
    return {'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f2': 5 * tp / (5 * tp + fp + 4 * fn) if tp + fp + fn else None}


def timely(gold, pred):
    if gold is None:
        return 'fp' if pred is not None else 'tn'
    if pred is None or pred > gold:
        return 'fn'
    return 'tp' if pred == gold else 'fp'


def key(entity):
    return (entity['message_id'], entity['start'], entity['end'], entity['type'])


def groups(doc):
    people = {}
    unknown = set()
    for entity in doc['entities']:
        if entity['person_id'] is None:
            unknown.add(key(entity))
        else:
            owners = entity['person_id'] if isinstance(entity['person_id'], list) else [entity['person_id']]
            for owner in owners:
                people.setdefault(owner, set()).add(key(entity))
    return Counter(frozenset(v) for v in people.values()), unknown


def compare(gold_dir, run):
    gold = {d['session_id']: d for d in read(gold_dir / 'gold.json')['annotations']}
    expected = {c['session_id']: c['assessment'] for c in read(gold_dir / 'gold-assessments.json')['checks']}
    manifest = read(run / 'manifest.json')
    if manifest['input_sha256'] != hashlib.sha256((gold_dir / 'sessions.json').read_bytes()).hexdigest():
        raise ValueError('Прогон выполнен на другом исходном наборе')
    counters, ext = Counter(), Counter()
    cases = []
    type_counts = {}
    flags_exact = raw_flags_exact = 0
    grouping_exact = 0
    fp0_n = fp0_fp = 0
    offsets = []
    valid_ids = set()
    for number, record in enumerate(manifest['records'], 1):
        item = {'session_id': record['session_id'], 'status': record['status']}
        if record['status'] != 'valid':
            item['error_type'] = record.get('error_type')
            item['http_status'] = record.get('http_status')
            cases.append(item)
            continue
        sid = record['session_id']
        pred = read(run / f'{number:02d}-annotation.json')
        result = read(run / f'{number:02d}-assessment.json')
        if sid not in gold or sid in valid_ids or pred['messages'] != gold[sid]['messages']:
            raise ValueError('Неверное сопоставление с эталоном')
        valid_ids.add(sid)
        gkeys, pkeys = set(map(key, gold[sid]['entities'])), set(map(key, pred['entities']))
        matched, missed, extra = gkeys & pkeys, gkeys - pkeys, pkeys - gkeys
        ext.update(tp=len(matched), fn=len(missed), fp=len(extra))
        for kind in {k[-1] for k in gkeys | pkeys}:
            row = type_counts.setdefault(kind, Counter())
            row.update(tp=sum(k[-1] == kind for k in matched), fn=sum(k[-1] == kind for k in missed), fp=sum(k[-1] == kind for k in extra))
        gfirst, pfirst = expected[sid]['first_alert'], result['first_alert']
        gi, pi = gfirst['message_index'] if gfirst else None, pfirst['message_index'] if pfirst else None
        session_outcome = 'tp' if gi is not None and pi is not None else 'fp' if pi is not None else 'fn' if gi is not None else 'tn'
        counters['session_' + session_outcome] += 1
        timely_outcome = timely(gi, pi)
        counters['timely_' + timely_outcome] += 1
        if gi is not None and pi is not None:
            offsets.append(pi - gi)
        if gi is None and not expected[sid]['case_flags']['annotation_uncertain']:
            fp0_n += 1
            fp0_fp += pi is not None
        owner_exact = groups(gold[sid]) == groups(pred)
        grouping_exact += owner_exact
        flags_match = expected[sid]['case_flags'] == result['case_flags']
        flags_exact += flags_match
        model_flags = pred['case_flags']
        if manifest.get('protocol') == 'sequential_user_only_v1':
            model_flags = read(run / f'{number:02d}-m{len(pred["messages"]):02d}-model-output.json')['case_flags']
        raw_flags_match = gold[sid]['case_flags'] == model_flags
        raw_flags_exact += raw_flags_match
        item.update(entity_counts={'matched': len(matched), 'missed': len(missed), 'extra': len(extra)},
                    missing_spans=sorted(missed), extra_spans=sorted(extra),
                    all_spans_and_ownership_exact=owner_exact, processed_flags_exact=flags_match,
                    model_flags_exact=raw_flags_match, model_flags=model_flags, gold_flags=gold[sid]['case_flags'],
                    gold_first_alert=gfirst, predicted_first_alert=pfirst,
                    session_outcome=session_outcome, timely_outcome=timely_outcome)
        cases.append(item)
    return {'model': manifest['requested_model'], 'run': str(run),
        'reference_status': 'author_llm_gold_not_human_verified',
        'total_gold_sessions': len(gold), 'attempted_sessions': len(cases), 'valid_sessions': len(valid_ids),
        'unattempted_session_ids': sorted(set(gold) - {r['session_id'] for r in manifest['records']}),
        'statuses': dict(Counter(r['status'] for r in manifest['records'])),
        'metric_scope': 'valid_sessions_only; request failures excluded with coverage, not silently negative',
        'extraction': score(ext['tp'], ext['fp'], ext['fn']),
        'extraction_by_type': {t: score(c['tp'], c['fp'], c['fn']) for t, c in sorted(type_counts.items())},
        'session': {**score(counters['session_tp'], counters['session_fp'], counters['session_fn']), 'tn': counters['session_tn']},
        'timely': {**score(counters['timely_tp'], counters['timely_fp'], counters['timely_fn']), 'tn': counters['timely_tn']},
        'fp0': {'fp': fp0_fp, 'confident_negative_sessions': fp0_n, 'value': fp0_fp / fp0_n if fp0_n else None},
        'offsets_messages': offsets,
        'all_spans_and_ownership_exact_sessions': grouping_exact,
        'processed_flags_exact_sessions': flags_exact,
        'model_flags_exact_sessions': raw_flags_exact,
        'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', type=Path, required=True)
    parser.add_argument('--runs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Проверить сохранённую сводку без перезаписи')
    args = parser.parse_args()
    markdown_path = args.output.with_suffix('.md')
    if not args.check and (args.output.exists() or markdown_path.exists()):
        parser.error('Укажите новый файл отчёта')
    results = [compare(args.gold, run) for run in args.runs]
    lines = ['# Сравнение LLM с авторским эталоном', '',
             'Небольшой синтетический набор. Эталон составлен LLM в текущей сессии, проверка человеком ожидается.',
             'Метрики ниже рассчитаны только на валидных ответах; покрытие и ошибки запросов указаны отдельно.', '']
    for result in results:
        lines += [f"## {result['model']}", '',
                  f"Валидные ответы: {result['valid_sessions']}/{result['total_gold_sessions']}; запросов: {result['attempted_sessions']}.",
                  f"Извлечение: `{json.dumps(result['extraction'])}`.",
                  f"Сессионный алерт: `{json.dumps(result['session'])}`.",
                  f"Первый алерт: `{json.dumps(result['timely'])}`.", '',
                  f"Исходные флаги модели совпали с эталоном в {result['model_flags_exact_sessions']} сессиях; после обработчика — в {result['processed_flags_exact_sessions']}.", '',
                  '| Сессия | Ответ | Спаны: совпало / пропущено / лишних | Группировка со спанами точная | Первый алерт: эталон → модель |',
                  '|---|---|---|---|---|']
        for case in result['cases']:
            if case['status'] != 'valid':
                lines += [f"| {case['session_id']} | {case['status']} / {case.get('error_type') or case.get('http_status')} | — | — | — |"]
                continue
            def first(value):
                return value['message_id'] + '/' + value['reason'] if value else 'нет'
            c = case['entity_counts']
            lines += [f"| {case['session_id']} | valid | {c['matched']} / {c['missed']} / {c['extra']} | {case['all_spans_and_ownership_exact']} | {first(case['gold_first_alert'])} → {first(case['predicted_first_alert'])} |"]
        lines += ['', f"Полные ответы и разметка: `{result['run']}`.", '']
    markdown = '\n'.join(lines)
    if args.check:
        if read(args.output) != results or markdown_path.read_text(encoding='utf-8') != markdown:
            raise ValueError('Сохранённая сводка отличается от текущих результатов сравнения')
    else:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        markdown_path.write_text(markdown, encoding='utf-8')
    print(json.dumps([{k: r[k] for k in ('model', 'valid_sessions', 'attempted_sessions', 'statuses', 'extraction', 'session', 'timely', 'all_spans_and_ownership_exact_sessions')} for r in results], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
