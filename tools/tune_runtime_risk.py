"""Подбор риска на сохранённых выходах NER/Gemma, с отдельной проверкой 90/90."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from evaluate_random_linker import alerts
from ownership import owner_ids
from relabel_weight_300 import digest, scoring_source
from session_pii.components import Fact
from session_pii.normalization import fact_value
from session_pii.risk import NAME_PARTS, PAIRS, RiskEvaluator, RiskPolicy


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def kinds(facts, unknown=False):
    personal = [f for f in facts if not f.service_contact]
    if unknown:
        return Counter(f.type for f in {(f.type, fact_value(f.type, f.value)): f for f in personal}.values())
    typed = {fact_value(f.type, f.value) for f in personal if f.type in NAME_PARTS}
    tokens = {token for value in typed for token in value.split()}
    active = {f.type for f in personal if f.type != 'person' or not (
        fact_value(f.type, f.value) in typed or (tokens and set(fact_value(f.type, f.value).split()) <= tokens))}
    parts = active & NAME_PARTS
    if len(parts) >= 2:
        active -= NAME_PARTS
        active.add('fio' if len(parts) == 3 else PAIRS[frozenset(parts)])
    return Counter(active)


def prepare(record, prediction, baseline):
    doc = record['gold']
    texts = {m['id']: m['text'] for m in doc['messages']}
    by_message = defaultdict(list)
    for i, e in enumerate(prediction['entities']):
        by_message[e['message_id']].append(Fact(e['type'], texts[e['message_id']][e['start']:e['end']],
            e['person_id'], str(i), 0, e.get('service_contact', False)))
    evaluator = RiskEvaluator(baseline)
    people, unknown, steps = defaultdict(list), [], []
    unknown_count = 0
    for message in doc['messages']:
        for fact in by_message[message['id']]:
            owners = owner_ids(fact.person_id)
            if owners:
                for owner in owners:
                    people[owner].append(fact)
            else:
                unknown.append(fact)
                unknown_count += not fact.service_contact
        states = []
        for is_unknown, facts in [(False, f) for f in people.values()] + [(True, unknown)]:
            assessment = evaluator.assess(tuple(facts), unknown=is_unknown)
            states.append(dict(unknown=is_unknown, features=dict(kinds(facts, is_unknown)),
                mandatory=any(r in assessment.rules for r in ('red_rule', 'combination_rule'))))
        steps.append(states)
    return dict(session_id=record['session_id'], family=record['family'], bucket=record['bucket'],
        expected=record['expected_first_alert'], saved_first_alert=prediction['first_alert'],
        uncertain=record['gold']['case_flags']['annotation_uncertain'], unknown_mentions=unknown_count,
        final_uncertain=record['calculator_assessment']['messages'][-1]['annotation_uncertain'], steps=steps)


def split_families(rows, seed):
    """Выбор по меткам и группам, до подбора и без метрик предсказаний."""
    families = sorted({r['family'] for r in rows})
    rng = random.Random(seed)
    target = len(rows) // 2
    best = None
    total_buckets = Counter(r['bucket'] for r in rows)
    total_positive = sum(r['expected'] is not None for r in rows)
    for _ in range(20000):
        selected = set(rng.sample(families, len(families)//2))
        train = [r for r in rows if r['family'] in selected]
        if len(train) != target:
            continue
        counts = Counter(r['bucket'] for r in train)
        balance = abs(2*sum(r['expected'] is not None for r in train)-total_positive)
        balance += sum(abs(2*counts[b]-n) for b, n in total_buckets.items())
        if best is None or balance < best[0]:
            best = balance, selected
            if balance == 0:
                break
    if best is None:
        raise ValueError('Cannot form equal disjoint-family splits')
    train = [r for r in rows if r['family'] in best[1]]
    test = [r for r in rows if r['family'] not in best[1]]
    return train, test


class Replay:
    def __init__(self, rows, keys):
        self.rows, self.keys = rows, keys
        self.gold = np.array([r['expected'] or 0 for r in rows])
        self.horizon = max(len(r['steps']) for r in rows)
        self.mandatory = np.zeros((len(rows), self.horizon), dtype=bool)
        features, known_indices, unknown_indices = [], [], []
        self.cells = []
        for i, row in enumerate(rows):
            for j, states in enumerate(row['steps']):
                for state in states:
                    features.append([state['features'].get(k, 0) for k in keys])
                    self.cells.append(i*self.horizon+j)
                    (unknown_indices if state['unknown'] else known_indices).append(len(features)-1)
                    self.mandatory[i, j] |= state['mandatory']
        self.features = np.array(features, dtype=np.int64)
        self.cells = np.array(self.cells)
        self.known, self.unknown = np.array(known_indices, dtype=int), np.array(unknown_indices, dtype=int)

    def predict(self, weights, person_threshold, unknown_threshold):
        # Целые сотые сохраняют строгое > без погрешности float на границе.
        scores = self.features @ weights
        known = np.zeros(self.gold.size*self.horizon, dtype=np.int64)
        unknown = np.zeros_like(known)
        np.maximum.at(known, self.cells[self.known], scores[self.known])
        np.maximum.at(unknown, self.cells[self.unknown], scores[self.unknown])
        active = self.mandatory | (known.reshape(self.mandatory.shape) > person_threshold)
        active |= unknown.reshape(self.mandatory.shape) > unknown_threshold
        return np.where(active.any(axis=1), active.argmax(axis=1)+1, 0)

    def metrics(self, params):
        pred = self.predict(*params)
        return alerts([int(v) if v else None for v in self.gold], [int(v) if v else None for v in pred])


def objective(gold, pred):
    tp = np.count_nonzero((gold > 0) & (pred == gold))
    fp = np.count_nonzero((pred > 0) & ((gold == 0) | (pred < gold)))
    fn = np.count_nonzero((gold > 0) & ((pred == 0) | (pred > gold)))
    timely = 5*tp/(5*tp+fp+4*fn) if 5*tp+fp+4*fn else 0
    stp = np.count_nonzero((gold > 0) & (pred > 0))
    sfp = np.count_nonzero((gold == 0) & (pred > 0))
    sfn = np.count_nonzero((gold > 0) & (pred == 0))
    session = 5*stp/(5*stp+sfp+4*sfn) if 5*stp+sfp+4*sfn else 0
    return float(timely), float(session)


def tune(train, baseline, seed, trials, *, guarded=False):
    bw, bp, bu = baseline
    evaluated = 0
    non_red = [i for i, key in enumerate(train.keys) if key not in
               {'passport', 'snils', 'individual_inn', 'bank_card'}]

    def rank(params):
        nonlocal evaluated
        evaluated += 1
        w, p, u = params
        if guarded and (any(w[i] < 5 or w[i] > p for i in non_red) or
                        max(w[i] for i in non_red) > u):
            return -1., -1., -10**9
        distance = int(np.abs(w-bw).sum())+abs(p-bp)+abs(u-bu)
        return (*objective(train.gold, train.predict(w, p, u)), -distance)

    best_threshold = baseline
    best_rank = rank(baseline)
    # Порог безусловного отключения численного пути неизвестных.
    unknown_cap = max(200, int(train.features[train.unknown].sum(axis=1).max(initial=0))*100)
    unknown_grid = sorted(set(range(0, 201, 5)) | {unknown_cap})
    for p in range(0, 201, 5):
        for u in unknown_grid:
            candidate = bw.copy(), p, u
            value = rank(candidate)
            if value > best_rank:
                best_rank, best_threshold = value, candidate
    print(f'Threshold grid complete: {evaluated} candidates', flush=True)
    # Масштаб зафиксирован порогом персоны 0.5; веса и порог не
    # умножаются одновременно на произвольную константу.
    active = [i for i in range(len(bw)) if train.features[:, i].any()]
    rng = np.random.default_rng(seed)
    pool = []
    seeds = [(bw.copy(), bp, bu), (bw.copy(), bp, best_threshold[2])]
    for _ in range(trials):
        weights = bw.copy()
        weights[active] = rng.integers(1 if guarded else 0,
            11 if guarded else (9 if rng.random() < .75 else 21), size=len(active))*5
        seeds.append((weights, bp, int(rng.choice(unknown_grid))))
    for params in seeds:
        pool.append((rank(params), params))
    pool.sort(key=lambda row: row[0], reverse=True)
    best_joint_rank, best_joint = pool[0]
    for restart, (_, initial) in enumerate(pool[:5], 1):
        current = initial
        current_rank = rank(current)
        for _ in range(3):
            improved = False
            for dimension in [*active, len(bw)]:
                local, local_rank = current, current_rank
                for value in (unknown_grid if dimension == len(bw) else
                              range(5, 51, 5) if guarded else range(0, 101, 5)):
                    weights, p, u = current[0].copy(), bp, current[2]
                    if dimension == len(bw):
                        u = value
                    else:
                        weights[dimension] = value
                    candidate = weights, p, u
                    candidate_rank = rank(candidate)
                    if candidate_rank > local_rank:
                        local, local_rank = candidate, candidate_rank
                if local_rank > current_rank:
                    current, current_rank, improved = local, local_rank, True
            if not improved:
                break
        if current_rank > best_joint_rank:
            best_joint_rank, best_joint = current_rank, current
        print(f'Weights restart {restart}/5 complete; candidates={evaluated}', flush=True)
    return best_threshold, best_joint, evaluated


def policy_from(params, baseline, keys, version):
    w, p, u = params
    return RiskPolicy(version=version, person_threshold=p/100, unknown_threshold=u/100,
        weights={k: int(v)/100 for k, v in zip(keys, w)})


def replay_actual(record, prediction, policy):
    evaluator = RiskEvaluator(policy)
    texts = {m['id']: m['text'] for m in record['gold']['messages']}
    people, unknown = defaultdict(list), []
    first = None
    for index, message in enumerate(record['gold']['messages'], 1):
        for i, e in enumerate(prediction['entities']):
            if e['message_id'] != message['id']:
                continue
            fact = Fact(e['type'], texts[e['message_id']][e['start']:e['end']], e['person_id'],
                        str(i), index, e.get('service_contact', False))
            owners = owner_ids(fact.person_id)
            if owners:
                for owner in owners:
                    people[owner].append(fact)
            else:
                unknown.append(fact)
        risks = [evaluator.assess(tuple(facts)) for facts in people.values()]
        risks.append(evaluator.assess(tuple(unknown), unknown=True))
        if any(r.rules for r in risks) and first is None:
            first = index
    return first


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seed', type=int, default=20261125)
    parser.add_argument('--trials', type=int, default=1500)
    parser.add_argument('--frozen-input', type=Path, help='Повторить на уже зафиксированных входах и разбиении')
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    if not all(p.is_relative_to(ROOT / 'data') for p in (run, output)):
        parser.error('Data paths must be inside data/')
    if output.exists():
        parser.error('Output must be a new directory to preserve the fixed split')
    original = read(run/'results/input-snapshot.json')
    source, _ = scoring_source(run, original)
    if not source.get('label_review'):
        parser.error('Requires corrected calculator reference labels')
    manifest = read(run/'results/manifest.json')
    complete = {r['session_id']: r for r in manifest['sessions'] if r['status'] == 'complete'}
    selected = [r for r in source['records'] if r['session_id'] in complete][:180]
    frozen_source = read(args.frozen_input) if args.frozen_input else None
    if frozen_source:
        if frozen_source['label_content_sha256'] != digest(source) or frozen_source['seed'] != args.seed:
            parser.error('Frozen input reference or seed mismatch')
        selected = frozen_source['selected_records']
    if len(selected) != 180:
        parser.error('Requires at least 180 completed sessions')
    baseline = RiskPolicy.model_validate(frozen_source['baseline_policy']) if frozen_source and 'baseline_policy' in frozen_source else RiskPolicy.load()
    if any(v is None for v in baseline.weights.values()):
        parser.error('Cannot fit missing weights')
    keys = sorted(baseline.weights)
    predictions = frozen_source['predictions'] if frozen_source else {
        r['session_id']: read(run/'results'/complete[r['session_id']]['path']) for r in selected}
    rows = [prepare(r, predictions[r['session_id']], baseline) for r in selected]
    train_rows, test_rows = split_families(rows, args.seed)
    if frozen_source:
        train_ids, test_ids = frozen_source['split']['train'], frozen_source['split']['test']
        if set(train_ids) & set(test_ids) or set(train_ids+test_ids) != {r['session_id'] for r in rows}:
            raise ValueError('Invalid frozen split')
        by_session = {r['session_id']: r for r in rows}
        train_rows, test_rows = [by_session[s] for s in train_ids], [by_session[s] for s in test_ids]
        if {r['family'] for r in train_rows} & {r['family'] for r in test_rows}:
            raise ValueError('Scenario families overlap across frozen split')
    train, test = Replay(train_rows, keys), Replay(test_rows, keys)
    params = np.array([round(baseline.weights[k]*100) for k in keys]), round(baseline.person_threshold*100), round(baseline.unknown_threshold*100)
    for replay in (train, test):
        if list(replay.predict(*params)) != [r['saved_first_alert'] or 0 for r in replay.rows]:
            raise ValueError('Replay disagrees with saved runtime baseline')
    output.mkdir(parents=True)
    split = {name: [r['session_id'] for r in group] for name, group in [('train', train_rows), ('test', test_rows)]}
    frozen = dict(seed=args.seed, split=split, label_content_sha256=digest(source),
        baseline_policy=baseline.model_dump(),
        prediction_sha256={sid: digest(pred) for sid, pred in predictions.items()},
        selected_records=selected, predictions=predictions)
    (output/'frozen-input.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Frozen split: 90/90, disjoint scenario families. Baseline replay verified.', flush=True)
    threshold_only, joint, evaluated = tune(train, params, args.seed, args.trials)
    _, guarded, guarded_evaluated = tune(train, params, args.seed, args.trials, guarded=True)
    unknown_only = params
    unknown_rank = (*objective(train.gold, train.predict(*params)), 0)
    for threshold in range(0, 201, 5):
        candidate = params[0], params[1], threshold
        rank = (*objective(train.gold, train.predict(*candidate)), -abs(threshold-params[2]))
        if rank > unknown_rank:
            unknown_only, unknown_rank = candidate, rank
    results = {}
    by_id = {r['session_id']: r for r in selected}
    settings = [('baseline', params), ('unknown_only', unknown_only), ('thresholds_only', threshold_only),
                ('tuned_weights_original_unknown', (joint[0], joint[1], params[2])), ('weights_and_unknown', joint),
                ('guarded_weights_and_unknown', guarded)]
    for name, setting in settings:
        policy = policy_from(setting, baseline, keys, 'experimental-'+name)
        for replay in (train, test):
            actual = [replay_actual(by_id[r['session_id']], predictions[r['session_id']], policy) or 0 for r in replay.rows]
            if actual != list(replay.predict(*setting)):
                raise ValueError('Optimized replay disagrees with RiskEvaluator')
        (output/(name+'-policy.json')).write_text(policy.model_dump_json(indent=2)+'\n', encoding='utf-8')
        results[name] = dict(policy=policy.model_dump(), train=train.metrics(setting), test=test.metrics(setting),
            deltas=dict(person_threshold=policy.person_threshold-baseline.person_threshold,
                unknown_threshold=policy.unknown_threshold-baseline.unknown_threshold,
                weights={k: round(policy.weights[k]-baseline.weights[k], 4) for k in keys}),
            test_predictions={r['session_id']: int(pred) or None for r, pred in zip(test.rows, test.predict(*setting))})
        results[name]['test_metric_delta_pp'] = {part: {metric: round(100*(results[name]['test'][part][metric]-
            results['baseline']['test'][part][metric]), 6) if results[name]['test'][part][metric] is not None and
            results['baseline']['test'][part][metric] is not None else None for metric in ('precision', 'recall', 'f2')}
            for part in ('session', 'timely')}
    def coverage(group):
        return dict(sessions=len(group), positives=sum(r['expected'] is not None for r in group),
            uncertain_at_end=sum(r['final_uncertain'] for r in group),
            families=sorted({r['family'] for r in group}), buckets=dict(Counter(r['bucket'] for r in group)),
            unknown_mentions=sum(r['unknown_mentions'] for r in group),
            sessions_with_unknown=sum(r['unknown_mentions'] > 0 for r in group))
    # Sensitivity is measured on training only; test does not select a threshold.
    sensitivity = []
    for threshold in range(0, 201, 5):
        candidate = joint[0], joint[1], threshold
        pred = train.predict(*candidate)
        sensitivity.append(dict(threshold=threshold/100, metrics=train.metrics(candidate),
            changed_sessions_vs_chosen=int(np.count_nonzero(pred != train.predict(*joint)))))
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), seed=args.seed,
        objective='maximize train F2-timely; tie: F2-session, then smallest L1 change from baseline',
        search='threshold grid 0..2 step .05; weights 0..1 step .05, randomized search + coordinate ascent; not a global optimum guarantee',
        weights_search_person_threshold_fixed=.5, candidates_evaluated=evaluated,
        guarded_candidates_evaluated=guarded_evaluated,
        guarded_constraints='Non-red weights .05..person threshold; unknown threshold >= max non-red weight; no single non-red fact alerts',
        split=split, coverage=dict(train=coverage(train_rows), test=coverage(test_rows)),
        label_policy_version=source['label_policy_version'], label_content_sha256=digest(source),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        frozen_input_sha256=hashlib.sha256((output/'frozen-input.json').read_bytes()).hexdigest(),
        baseline_config_sha256=hashlib.sha256((ROOT/'configs/runtime-risk-policy.json').read_bytes()).hexdigest(),
        single_attribute_alerts=[k for k, w in results['weights_and_unknown']['policy']['weights'].items()
            if w > results['weights_and_unknown']['policy']['person_threshold'] and
            k not in {'passport', 'snils', 'individual_inn', 'bank_card'}],
        results=results, unknown_threshold_sensitivity_train=sensitivity,
        mandatory_rules_preserved=True, runtime_config_changed=False,
        limitations=['Small synthetic template-based split; no confidence claim for production.',
            'Negative labels with insufficient statistics reflect working policy, not proven safety.',
            'Weights are relative scores, not calibrated probabilities.',
            'Only saved successful sessions; inference failures excluded.',
            'Unknown threshold can be unidentifiable when insufficient unknown-owner examples.'])
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# Подбор риска: 90 сессий для подбора, 90 для проверки', '',
        'Разбиение зафиксировано до поиска; семейства сценариев не пересекаются. '
        'NER и Gemma не перезапускаются. Обязательные правила сохранены. Рабочий конфиг не меняется.', '',
        '| Вариант | Часть | Precision session | Recall session | F2-session | Precision timely | Recall timely | F2-timely |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    percent = lambda x: 'N/A' if x is None else f'{x*100:.2f}%'
    for name, row in results.items():
        for part in ('train', 'test'):
            s, t = row[part]['session'], row[part]['timely']
            lines.append('| '+' | '.join([name, part, *[percent(s[k]) for k in ('precision', 'recall', 'f2')],
                *[percent(t[k]) for k in ('precision', 'recall', 'f2')]])+' |')
    lines.extend(['', '| Параметр | Исходный | Только пороги | Свободные веса | Дельта | С ограничениями |', '|---|---:|---:|---:|---:|---:|'])
    for k in ['person_threshold', 'unknown_threshold', *keys]:
        get = lambda row: row['policy'][k] if k.endswith('_threshold') else row['policy']['weights'][k]
        values = [get(results[n]) for n in ('baseline', 'thresholds_only', 'weights_and_unknown', 'guarded_weights_and_unknown')]
        lines.append(f'| {k} | {values[0]:.2f} | {values[1]:.2f} | {values[2]:.2f} | {values[2]-values[0]:+.2f} | {values[3]:.2f} |')
    lines.extend(['', 'Порог известной персоны при подборе весов зафиксирован, чтобы исключить '
        'неопределённость общего масштаба весов и порога. Лучший найденный результат не гарантирует глобального оптимума.', '',
        'Числа, поддержка неизвестных владельцев, неопределённость эталона, списки ID и чувствительность '
        'порога неизвестных сохранены в [report.json](report.json). Полные неизменяемые входы — '
        '[frozen-input.json](frozen-input.json).', '',
        'Отрицательные метки при недостатке статистики не доказывают безопасность. '
        'Результат нельзя без дополнительной проверки переносить в рабочие веса.', ''])
    lines.extend(['## Проверка нежелательного поведения', '',
        'Найденные свободным поиском веса позволяют одиночным атрибутам вызвать алерт: '+
        ', '.join(report['single_attribute_alerts'])+'. Это противоречит ограничению достаточности '
        'одиночного контакта/адреса и показывает, почему оптимум F2 нельзя автоматически переносить в сервис.', '',
        'Отдельный поиск guarded_weights_and_unknown сохраняет положительный вес каждого типа и '
        'запрещает одиночному некрасному атрибуту превысить порог известного или неизвестного владельца. '
        'Остальные параметры и разбиение те же. Ограничения заданы по смыслу правил; test не участвует в подборе.', '',
        'Изменение только порога неизвестных и возврат исходного порога при новых весах '
        'показаны отдельными строками для проверки его вклада. Чувствительность вычислена на train. '
        'Выбор настроек по test не выполнялся.', ''])
    (output/'review.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({k: dict(train=v['train'], test=v['test'], deltas=v['deltas']) for k, v in results.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
