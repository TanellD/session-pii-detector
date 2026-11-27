"""Отчёт покрытия, побочные числовые признаки и контроль целостности корпуса."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from build_session_benchmark import read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    if not dataset.is_relative_to(ROOT/'data'):
        parser.error('Dataset must remain under data/')
    source = read(dataset/'snapshot.json')
    validation = read(dataset/'validation-report.json')
    rows = source['records']
    samples = read(dataset/'review-sample.json')['records']
    cells, codes, spans, features = {}, defaultdict(list), Counter(), Counter()
    for row in rows:
        doc = row['gold']
        outcome = 'positive' if row['expected_first_alert'] is not None else 'negative_uncertain' if row['final_uncertain'] else 'negative_certain'
        key = f'{row["split"]}/{row["people_count"]}/{row["length_group"]}'
        cell = cells.setdefault(key, dict(sessions=0, outcomes=Counter(), types=Counter(), first_alert_positions=Counter()))
        cell['sessions'] += 1
        cell['outcomes'][outcome] += 1
        cell['types'].update(e['type'] for e in doc['entities'])
        if row['expected_first_alert'] is not None:
            cell['first_alert_positions'][row['expected_first_alert']] += 1
        by_mid = {m['id']: m['text'] for m in doc['messages']}
        seen = set()
        for entity in doc['entities']:
            value = by_mid[entity['message_id']][entity['start']:entity['end']]
            feature = entity['type'], value, json.dumps(entity['person_id'])
            features['repeated_mentions'] += feature in seen
            seen.add(feature)
            features['shared_mentions'] += isinstance(entity['person_id'], list)
            features['unknown_mentions'] += entity['person_id'] is None and entity['id'] not in row['gold_service_contacts']
            features['service_mentions'] += entity['id'] in row['gold_service_contacts']
            if entity['type'] == 'sex':
                spans[value] += 1
            if entity['type'] == 'phone':
                match = re.search(r'\+7 \(000\) (\d{3})-', value)
                if match:
                    codes[row['split']+'/'+('positive' if outcome == 'positive' else 'negative')].append(int(match[1]))
    export_counts = {}
    for split in ('train', 'validation', 'test'):
        export_counts[split] = {}
        for name in ('ner.jsonl', 'linker-supervised.jsonl', 'alerts.jsonl'):
            with (dataset/split/name).open(encoding='utf-8') as stream:
                export_counts[split][name] = sum(1 for _ in stream)
    summary = dict(version=source['version'], sessions=len(rows), messages=sum(len(r['gold']['messages']) for r in rows),
                   entities=sum(len(r['gold']['entities']) for r in rows), cells=cells,
                   sex_mentions=dict(spans), features=dict(features), exports=export_counts,
                   review_sample=dict(sessions=len(samples), cells=dict(Counter(f'{r["split"]}/{r["people_count"]}/{r["length_group"]}' for r in samples))),
                   phone_code_ranges={key: [min(values), max(values)] for key, values in codes.items()},
                   numeric_audit_limit='Descriptive ranges only; independent allocation is verified in generator, not inferred from overlapping ranges.',
                   test_protocol='No tuning on test; messages-only sessions for end-to-end evaluation; gold-prefix linker exports only for isolated supervised work.')
    if args.review and (args.review/'summary.json').exists():
        review = read(args.review/'summary.json')
        identity = read(args.review/'provenance.json') if (args.review/'provenance.json').exists() else read(args.review/'manifest.json')
        assert identity.get('final_sample_sha256', identity.get('input_sha256')) == hashlib.sha256((dataset/'review-sample.json').read_bytes()).hexdigest()
        summary['review'] = {k: review[k] for k in ('completed', 'total', 'verdicts', 'issue_categories')}
    write(dataset/'coverage-report.json', summary)
    lines = ['# Синтетический набор для NER, линкера и сессионных алертов', '',
             f'Версия `{source["version"]}`. Только синтетика. Авторские композиционные заготовки, '
             'программная подстановка значений и владельцев, алерты калькулятора. '
             'Это исходный исследовательский набор, не измерение качества на реальных чатах.', '',
             '| Часть | Сессий | Сообщений | Сущностей | С алертом | Без алерта, определённо | Без алерта, недостаток статистики |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for split, values in validation['splits'].items():
        outcomes = values['outcomes']
        lines.append(f'| {split} | {values["sessions"]} | {values["messages"]} | {values["entities"]} | '
                     f'{outcomes.get("positive",0)} | {outcomes.get("negative_certain",0)} | {outcomes.get("negative_uncertain",0)} |')
    lines += ['', 'В каждой части восемь ячеек: 0/1/2/3 человека × 2–5/6–8 сообщений. '
              'На ячейку приходится 180 train, 60 validation и 60 test. '
              'Все поддерживаемые типы представлены в каждой части; таблица частот — в coverage-report.json.', '',
              '## Файлы и применение', '',
              '- `train|validation|test/sessions.json` — вход полного сервиса, только ID и сообщения. Не содержит владельцев, числа людей и ожидаемых алертов.',
              '- `gold.json` — исходные спаны и владельцы по annotation-mvp-v2.',
              '- `ner.jsonl` — один текст сообщения и спаны; включает сообщения без сущностей.',
              '- `linker-supervised.jsonl` — обучающие префиксы с эталонными кандидатами и прошлым эталонным состоянием. '
              'Новые владельцы представлены new:N, неизвестные owners=[], общий атрибут имеет нескольких владельцев. '
              'Это teacher forcing для обучения/изолированной проверки; его метрики не являются качеством полного сервиса.',
              '- `alerts.jsonl` — все эталонные префиксные решения и первый алерт. Веса сервиса не использовались.',
              '- `people-N/short|long/session-ids.json` внутри каждой части — состав группы для раздельных метрик.',
              '- `calculator-assessments.json`, `address-enrichment.json` — расчёты и авторские подтверждения квартир; '
              'фактическая заселённость вымышленных адресов не известна.',
              '- `review-sample.md` — читаемые тексты и разметка стратифицированной выборки; '
              '`review-sample.json` — вход слепого ревью, которое получает только тексты.',
              '- `coverage-report.json`, `validation-report.json`, `manifest.json` — покрытие и проверки.', '',
              '## Разделение и ограничения', '',
              'Train используется для обучения, validation — для выбора параметров, test — для итогового замера после фиксации конфигурации. '
              'Не переносите просмотренные тестовые ошибки в правила с повторным отчётом на том же test как на свежем. '
              'Полные профили и сюжетные семейства разделены; широкие механизмы владения и типы атрибутов общие.', '',
              'Неточное сходство сюжетов не исключается проверкой точных дублей. '
              'Программные варианты одного сюжета статистически зависимы; число сессий не равно числу независимых историй. '
              'В каждой части всего пять сюжетных семейств, а редкие типы представлены небольшим числом упоминаний. '
              'Нужна последующая проверка на отдельном источнике данных. '
              'Отрицательные с недостатком статистики не доказывают безопасность. '
              'Ревью выборочное, независимого человеческого подтверждения нет.', '',
              'Готовность и разбор замечаний сохраняются отдельно в readiness.json. '
              'Исходные манифесты не переписываются после ревью.', '']
    (dataset/'overview.md').write_text('\n'.join(lines), encoding='utf-8')
    immutable = ['snapshot.json', 'generation-plan.json', 'manifest.json', 'calculator-assessments.json', 'address-enrichment.json', 'review-sample.json']
    immutable += [str(p.relative_to(dataset)) for split in ('train', 'validation', 'test') for p in (dataset/split).rglob('*') if p.is_file()]
    seal = {name: hashlib.sha256((dataset/name).read_bytes()).hexdigest() for name in sorted(immutable)}
    if (dataset/'frozen-sha256.json').exists():
        assert read(dataset/'frozen-sha256.json') == seal, 'Frozen artifacts changed'
    else:
        write(dataset/'frozen-sha256.json', seal)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('cells',)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
