"""Подбор весов и порогов только по сохранённому validation, без моделей/API."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools'), str(ROOT)]
from evaluate_random_linker import alerts
from session_pii.risk import RiskPolicy
from tune_runtime_risk import Replay, prepare, objective, policy_from, replay_actual

RED = {'passport', 'snils', 'individual_inn', 'bank_card'}
DERIVED = {'fi', 'io', 'fo', 'fio'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def measured(rows, predictions):
    expected = [r['expected'] for r in rows]
    result = alerts(expected, predictions)
    result['fp_negative_all'] = result.pop('fp0')
    definite = [i for i, r in enumerate(rows) if r['expected'] is None and not r['uncertain']]
    result['fp0'] = sum(predictions[i] is not None for i in definite)/len(definite) if definite else None
    result['fp0_denominator'] = len(definite)
    result['uncertain_sessions'] = sum(r['uncertain'] for r in rows)
    result['fp0_definition'] = 'false alerts / confidently negative sessions; fp_negative_all additionally includes uncertain negatives'
    return result


def search(replay, baseline, trials, seed, thresholds_only=False):
    bw, bp, bu = baseline
    raw = [i for i, key in enumerate(replay.keys) if key not in RED | DERIVED]
    active = [i for i, key in enumerate(replay.keys) if key not in RED and replay.features[:, i].any()]
    cap = max(200, int(replay.features.sum(axis=1).max(initial=0))*100)
    grid = sorted(set(range(0, 201, 5)) | {cap})
    evaluated = 0

    def rank(params):
        nonlocal evaluated
        evaluated += 1
        weights, p, u = params
        maximum_raw = max((int(weights[i]) for i in raw), default=0)
        if min(p, u) < maximum_raw or any(weights[i] < 5 for i in raw):
            return (-1., -1., -1e12)
        distance = int(np.abs(weights-bw).sum())+abs(p-bp)+abs(u-bu)
        return (*objective(replay.gold, replay.predict(weights, p, u)), -distance)

    threshold_best, best_rank = baseline, rank(baseline)
    for p in grid:
        for u in grid:
            candidate = bw.copy(), p, u
            candidate_rank = rank(candidate)
            if candidate_rank > best_rank:
                threshold_best, best_rank = candidate, candidate_rank
    print(f'Threshold candidates: {evaluated}', flush=True)
    if thresholds_only:
        return threshold_best, threshold_best, evaluated
    rng = np.random.default_rng(seed)
    seeds = [baseline, (bw.copy(), bp, threshold_best[2])]
    for _ in range(trials):
        weights = bw.copy()
        for i in active:
            weights[i] = int(rng.integers(1, 21 if replay.keys[i] in DERIVED else 11))*5
        minimum = max(int(weights[i]) for i in raw)
        seeds.append((weights, bp, int(rng.choice([v for v in grid if v >= minimum]))))
    pool = sorted([(rank(p), p) for p in seeds], key=lambda item:item[0], reverse=True)
    joint_rank, joint = pool[0]
    for _, initial in pool[:3]:
        current, current_rank = initial, rank(initial)
        for _ in range(2):
            changed = False
            for dimension in [*active, len(bw)]:
                local, local_rank = current, current_rank
                values = grid if dimension == len(bw) else range(5, 101 if replay.keys[dimension] in DERIVED else bp+1, 5)
                for value in values:
                    weights, p, u = current[0].copy(), bp, current[2]
                    if dimension == len(bw):
                        u = value
                    else:
                        weights[dimension] = value
                    params = weights, p, u
                    candidate_rank = rank(params)
                    if candidate_rank > local_rank:
                        local, local_rank = params, candidate_rank
                if local_rank > current_rank:
                    current, current_rank, changed = local, local_rank, True
            if not changed:
                break
        if current_rank > joint_rank:
            joint, joint_rank = current, current_rank
    return threshold_best, joint, evaluated


def load_rows(run, allow_incomplete=False):
    source = read(run/'input-snapshot.json')
    if any(r.get('split') != 'validation' for r in source['records']):
        raise ValueError('Do not fit on train/test or a mixed snapshot')
    baseline = RiskPolicy.model_validate(read(run/'baseline-policy.json'))
    records, predictions, prepared = [], {}, []
    for original in source['records']:
        path = run/'predictions'/(original['session_id']+'.json')
        if not path.exists():
            if allow_incomplete:
                continue
            raise ValueError('Incomplete validation; do not silently exclude failed sessions')
        prediction = read(path)
        if prediction.get('status') != 'complete':
            raise ValueError('Incomplete prediction')
        row = dict(original, bucket=f'people-{original["people_count"]}/{original["length_group"]}',
                   calculator_assessment=dict(messages=[dict(annotation_uncertain=original['final_uncertain'])]))
        records.append(row)
        predictions[row['session_id']] = prediction
        prepared.append(prepare(row, prediction, baseline))
    return source, records, predictions, prepared, baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--policy', type=Path, help='Only rescore this policy; no search and no model calls')
    parser.add_argument('--trials', type=int, default=1200)
    parser.add_argument('--seed', type=int, default=20261126)
    parser.add_argument('--allow-incomplete', action='store_true', help='Provisional fit only, with explicit coverage; at least 90 percent required')
    parser.add_argument('--thresholds-only', action='store_true', help='Keep all attribute weights fixed; exhaustive threshold grid only')
    args = parser.parse_args()
    run = args.run.resolve()
    output = args.output.resolve() if args.output else run/'calibration'
    if not run.is_relative_to(ROOT/'data') or not output.is_relative_to(ROOT/'data'):
        parser.error('Run/output must be under data/')
    source, records, predictions, rows, baseline = load_rows(run, args.allow_incomplete)
    total = len(source['records'])
    if len(records) < .9*total:
        raise ValueError('Coverage below 90 percent; do not fit on a heavily selected subset')
    keys = sorted(baseline.weights)
    replay = Replay(rows, keys)
    params = np.array([round(baseline.weights[k]*100) for k in keys]), round(baseline.person_threshold*100), round(baseline.unknown_threshold*100)
    if list(replay.predict(*params)) != [predictions[r['session_id']]['first_alert'] or 0 for r in records]:
        raise ValueError('Baseline replay differs from saved full pipeline')
    if args.policy:
        policy = RiskPolicy.model_validate(read(args.policy))
        values = [replay_actual(row, predictions[row['session_id']], policy) for row in records]
        report = dict(policy=policy.model_dump(), validation=measured(rows, values),
                      sessions=len(records), inference_repeated=False, policy_sha256=hashlib.sha256(args.policy.read_bytes()).hexdigest())
        write(output/('replay-'+report['policy_sha256'][:12]+'.json'), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if output.exists():
        raise ValueError('Use a new output directory for a new search; --policy can rescore separately')
    thresholds, joint, candidates = search(replay, params, args.trials, args.seed, args.thresholds_only)
    results = {}
    for name, setting in [('baseline', params), ('thresholds_only', thresholds), ('weights_and_unknown', joint)]:
        if args.thresholds_only and name == 'weights_and_unknown':
            continue
        policy = policy_from(setting, baseline, keys, 'validation-candidate-'+name)
        fast = [int(v) or None for v in replay.predict(*setting)]
        actual = [replay_actual(row, predictions[row['session_id']], policy) for row in records]
        if fast != actual:
            raise ValueError('Fast replay differs from actual RiskEvaluator')
        by_group = {}
        for bucket in sorted({r['bucket'] for r in rows}):
            selected = [i for i, row in enumerate(rows) if row['bucket'] == bucket]
            by_group[bucket] = measured([rows[i] for i in selected], [actual[i] for i in selected])
        results[name] = dict(policy=policy.model_dump(), validation=measured(rows, actual), by_group=by_group,
                             deltas=dict(person_threshold=policy.person_threshold-baseline.person_threshold,
                                         unknown_threshold=policy.unknown_threshold-baseline.unknown_threshold,
                                         weights={k: round(policy.weights[k]-baseline.weights[k], 6) for k in keys}),
                             first_alerts={r['session_id']: value for r, value in zip(records, actual)})
        write(output/(name+'-policy.json'), policy.model_dump())
    for result in results.values():
        result['delta_pp'] = {part:{metric:100*(result['validation'][part][metric]-results['baseline']['validation'][part][metric])
                                   if result['validation'][part][metric] is not None and results['baseline']['validation'][part][metric] is not None else None
                                   for metric in ('precision', 'recall', 'f2')} for part in ('session', 'timely')}
    def selected_rank(name):
        row = results[name]
        delta = row['deltas']
        distance = sum(abs(v) for v in delta['weights'].values())+abs(delta['person_threshold'])+abs(delta['unknown_threshold'])
        return row['validation']['timely']['f2'] or 0, row['validation']['session']['f2'] or 0, -round(distance, 8)
    recommended = max(results, key=selected_rank)
    write(output/'recommended-policy.json', results[recommended]['policy'])
    report = dict(sessions=len(records), source_sessions=total, provisional=len(records) != total,
                  excluded_failed_sessions=[r['session_id'] for r in source['records'] if r['session_id'] not in predictions],
                  split='validation', seed=args.seed, candidates=candidates, thresholds_only=args.thresholds_only,
                  objective='F2-timely, tie F2-session, tie minimum L1 change', results=results,
                  recommended_variant=recommended,
                  inactive_weights=[key for i, key in enumerate(keys) if not replay.features[:, i].any()],
                  constraints='Red/combination rules retained; no single raw non-red fact alerts; weight search fixes known-person threshold scale.',
                  test_used=False, production_config_changed=False, inference_repeated=False,
                  snapshot_sha256=hashlib.sha256((run/'input-snapshot.json').read_bytes()).hexdigest(),
                  baseline_sha256=hashlib.sha256((run/'baseline-policy.json').read_bytes()).hexdigest(),
                  prediction_sha256={sid: hashlib.sha256((run/'predictions'/(sid+'.json')).read_bytes()).hexdigest() for sid in predictions},
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  coverage=dict(uncertain=sum(r['uncertain'] for r in rows), groups=dict(Counter(r['bucket'] for r in rows))),
                  limitations=['Metrics on fitting validation are optimistic; frozen test is still required.',
                               'Synthetic templates; five narrative families; many negative labels have insufficient statistics.',
                               'Random/coordinate search is not a proof of global optimality.'])
    write(output/'report.json', report)
    baseline_metrics = read(run/'baseline-metrics.json') if (run/'baseline-metrics.json').exists() else {}
    baseline_metrics['alerts'] = results['baseline']['validation']
    baseline_metrics['fp0_protocol'] = 'Confidently negative sessions only; all-negative rate separately retained as fp_negative_all.'
    write(output/'baseline-metrics-conformant.json', baseline_metrics)
    lines = ['# Подбор риска на сохранённом validation', '',
             f'Завершено {len(records)}/{total}. '+('Предварительный подбор на выбранном успешном подмножестве; ошибки исключены, не считаются TN.' if len(records) != total else 'Все сессии завершены.'), '',
             'NER и Gemma повторно не вызывались. Test не использован; конфиг сервиса не изменён.', '',
             '| Вариант | P-session | R-session | F2-session | P-timely | R-timely | F2-timely |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name, result in results.items():
        metrics = result['validation']
        values = [metrics[part][key] for part in ('session', 'timely') for key in ('precision', 'recall', 'f2')]
        lines.append('| '+name+' | '+' | '.join(f'{v:.4f}' if v is not None else '—' for v in values)+' |')
    lines += ['', 'Все параметры, дельты, групповые метрики и моменты алерта: report.json.',
              'Результаты подбора оптимистичны: итоговая проверка проводится позже на test после выбора одной политики.', '']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(sessions=len(records), output=str(output), candidates=candidates), ensure_ascii=False))


if __name__ == '__main__':
    main()
