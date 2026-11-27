"""Фиксированный исправленный срез Gemma: компоненты и случайный baseline сервиса."""
import argparse
from collections import Counter
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from annotation_contract import assess, validate
from annotation_calculator import Calculator
from compare_annotation_models import score, timely
from ownership import owner_ids
from benchmarks.pii_smoke.regex_detector import valid_inn, valid_snils, valid_luhn
from session_pii.components import DetectionResult, EmptyDetector, Fact, LinkResult
from session_pii.detectors import DetectorSettings, build_detectors
from session_pii.models import Candidate, Message, Span, Verdict
from session_pii.pipeline import Pipeline
from session_pii.random_linker import RandomLinker


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def session_seed(seed, session_id):
    # Разные потоки между сессиями: одинаковый seed на каждой короткой сессии
    # дал бы искусственно коррелирующие последовательности решений.
    return int.from_bytes(hashlib.sha256(f'{seed}:{session_id}'.encode()).digest()[:8], 'big')


def key(e):
    return e['message_id'], e['start'], e['end'], e['type']


class ReferenceDetector:
    def __init__(self, document):
        self.document = document

    def detect(self, message):
        mid = self.document['messages'][message.message_id - 1]['id']
        return DetectionResult(tuple(Span(type=e['type'], start=e['start'], end=e['end'])
            for e in self.document['entities'] if e['message_id'] == mid))


class RecordedDetector:
    """Выход реального детектора по тексту; без gold, владельцев и истории."""
    def __init__(self, outputs):
        self.outputs = outputs

    def detect(self, message):
        return self.outputs[message.text]


def recording_identity(docs,settings):
    texts=sorted({hashlib.sha256(m['text'].encode()).hexdigest() for d in docs for m in d['messages']})
    return dict(mode=settings.mode,config_sha256=hashlib.sha256(settings.config_path.read_bytes()).hexdigest(),
        texts_sha256=hashlib.sha256(json.dumps(texts).encode()).hexdigest(),
        prepared_manifest_sha256=hashlib.sha256((settings.model_dir/'manifest.json').read_bytes()).hexdigest()
            if settings.mode=='gliner' else None)


def record_detector(docs, settings, directory, reuse=False):
    """Инференс независим от линкера: повторные seed используют те же спаны."""
    identity=recording_identity(docs,settings)
    if reuse:
        if read(directory/'detector-recordings-identity.json')!=identity:
            raise ValueError('Записанный выход относится к другому конфигу, модели или текстам')
        records=read(directory/'detector-recordings.json')
        by_hash={r['text_sha256']:r for r in records}
        texts={m['text'] for d in docs for m in d['messages']}
        assert len(by_hash)==len(records)==len(texts)
        outputs={}
        for text in texts:
            row=by_hash[hashlib.sha256(text.encode()).hexdigest()]
            outputs[text]=DetectionResult(tuple(Span.model_validate_json(json.dumps(e)) for e in row['spans']),
                row['complete'],tuple(row['reasons']))
        return RecordedDetector(outputs),records
    if settings.mode == 'gliner':
        import torch
        torch.set_num_threads(read(settings.config_path)['threads'])
    detector, _ = build_detectors(settings)
    outputs, records = {}, []
    for doc in docs:
        for i,m in enumerate(doc['messages'],1):
            if m['text'] in outputs:
                continue
            start = time.perf_counter()
            result = detector.detect(Message(message_id=i,text=m['text']))
            elapsed = (time.perf_counter()-start)*1000
            outputs[m['text']] = result
            records.append(dict(text_sha256=hashlib.sha256(m['text'].encode()).hexdigest(),
                spans=[e.model_dump(mode='json') for e in result.spans], complete=result.complete,
                reasons=result.reasons, latency_ms=elapsed))
            if len(records) % 25 == 0:
                print(f'Inference completed: {len(records)} unique texts',flush=True)
    write(directory/'detector-recordings.json',records)
    write(directory/'detector-recordings-identity.json',identity)
    return RecordedDetector(outputs), records


class ReferenceLinker:
    """Только изолированная оценка риска: явно подставленные эталонные владельцы."""
    def __init__(self, document, family):
        self.document, self.family = document, family

    def link(self, message, candidates, known_facts):
        mid = self.document['messages'][message.message_id - 1]['id']
        lookup = {key(e): e['person_id'] for e in self.document['entities']}
        owners = tuple(lookup[(mid, c.start, c.end, c.type)] for c in candidates)
        flags = tuple(self.family == 'service_contacts' and c.type in {'phone', 'email'} for c in candidates)
        return LinkResult(owners, service_contacts=flags)


def extraction(gold, predictions):
    total, types = Counter(), {}
    for doc, pred in zip(gold, predictions):
        g, p = Counter(map(key, doc['entities'])), Counter(map(key, pred))
        for k in g.keys() | p.keys():
            counts = dict(tp=min(g[k], p[k]), fp=max(0, p[k]-g[k]), fn=max(0, g[k]-p[k]))
            total.update(counts)
            types.setdefault(k[-1], Counter()).update(counts)
    return dict(micro=score(total['tp'], total['fp'], total['fn']),
                by_type={t: score(c['tp'], c['fp'], c['fn']) for t,c in sorted(types.items())})


def pairwise(documents, predictions):
    counts = Counter()
    excluded = eligible = 0
    for doc, pred in zip(documents, predictions):
        assert len(doc['entities']) == len(pred)
        selected = [i for i,e in enumerate(doc['entities']) if owner_ids(e['person_id'])]
        eligible += len(selected)
        excluded += len(pred) - len(selected)
        for i,j in itertools.combinations(selected, 2):
            g = bool(set(owner_ids(doc['entities'][i]['person_id'])) & set(owner_ids(doc['entities'][j]['person_id'])))
            p = bool(set(owner_ids(pred[i]['person_id'])) & set(owner_ids(pred[j]['person_id'])))
            counts['tp' if g and p else 'fn' if g else 'fp' if p else 'tn'] += 1
    tp,fp,fn = (counts[k] for k in ('tp','fp','fn'))
    return dict(counts=counts, precision=tp/(tp+fp) if tp+fp else None,
        recall=tp/(tp+fn) if tp+fn else None, f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
        eligible_mentions=eligible, excluded_gold_unknown_mentions=excluded,
        aggregation='micro; final state; mention pairs within session; unknown predicts no positive pairs')


def alerts(expected, predicted):
    session, timing = Counter(), Counter()
    offsets = []
    for g,p in zip(expected,predicted):
        session['tp' if g is not None and p is not None else 'fn' if g is not None else 'fp' if p is not None else 'tn'] += 1
        timing[timely(g,p)] += 1
        if g is not None and p is not None:
            offsets.append(p-g)
    def scored(c):
        return {**score(c['tp'],c['fp'],c['fn']), 'tn':c['tn']}
    return dict(session=scored(session), timely=scored(timing),
        fp0=session['fp']/(session['fp']+session['tn']) if session['fp']+session['tn'] else None,
        offsets=offsets, exact_rate=sum(x==0 for x in offsets)/len(offsets) if offsets else None)


def run_pipeline(doc, family, seed, settings, isolated=False, reference_spans=False, recorded=None):
    detector, regexp = (ReferenceDetector(doc), EmptyDetector()) if isolated or reference_spans else (
        (recorded, EmptyDetector()) if recorded is not None else build_detectors(settings))
    linker = ReferenceLinker(doc, family) if isolated else RandomLinker(session_seed(seed, doc['session_id']))
    pipe = Pipeline(detector, regexp, linker)
    facts, verdict, entities, elapsed = (), Verdict(), [], []
    error=None
    for i,m in enumerate(doc['messages'],1):
        start = time.perf_counter()
        try:
            step = pipe.process(Message(message_id=i,text=m['text']), facts, verdict,
                                session_id=doc['session_id'], session_epoch='evaluation')
        except ValueError as exc:
            error=dict(message_index=i,error_type=type(exc).__name__,reason=str(exc))
            break
        elapsed.append((time.perf_counter()-start)*1000)
        facts, verdict = step.facts, step.verdict
        entities.extend(dict(message_id=m['id'],start=e.start,end=e.end,type=e.type,person_id=e.person_id)
                        for e in step.entities)
    return dict(entities=entities,first_alert=verdict.first_alert_message_id,
                coverage_complete=verdict.coverage_complete and error is None,
                incomplete_reasons=verdict.incomplete_reasons+(('runtime_contract_error',) if error else ()),
                latency_ms=elapsed,runtime_error=error)


def prepare(directory):
    if (directory/'corrected-gold.json').exists():
        raise ValueError('Исправленный эталон уже существует; используйте запуск без --prepare')
    snapshot = read(directory/'snapshot.json')
    assert len(snapshot['records']) == 250
    documents, ledger = [], []
    for item in snapshot['records']:
        doc = copy.deepcopy(item['gold'])
        validate(doc)
        # По разрешению пользователя используем авторский эталон, а не ответ Gemma.
        # Неоднозначные роли сохраняют назначение сценария; это не человеческий gold.
        texts = {m['id']:m['text'] for m in doc['messages']}
        ambiguous = [e['id'] for e in doc['entities'] if texts[e['message_id']][e['start']:e['end']] in {'Шаблоний','МакетныйСтранник'}]
        ledger.append(dict(session_id=doc['session_id'],family=item['family'],
            reference='author_annotation_reused_as_requested',
            gemma_differences=item['comparison'],scenario_dependent_entities=ambiguous,
            review='family templates and Gemma disagreements reviewed; ambiguous role retained from authored scenario'))
        documents.append(doc)
    write(directory/'corrected-gold.json',documents)
    write(directory/'review-ledger.json',ledger)


def isolated_linker(docs, seed):
    predictions = []
    for doc in docs:
        linker, facts, pred = RandomLinker(session_seed(seed, doc['session_id'])), (), []
        for i,m in enumerate(doc['messages'],1):
            rows = [e for e in doc['entities'] if e['message_id']==m['id']]
            candidates = tuple(Candidate(type=e['type'],start=e['start'],end=e['end']) for e in rows)
            linked = linker.link(Message(message_id=i,text=m['text']),candidates,facts)
            pred.extend({**e,'person_id':p} for e,p in zip(rows,linked.person_ids))
            facts += tuple(Fact(e['type'],m['text'][e['start']:e['end']],p,e['id'],i) for e,p in zip(rows,linked.person_ids))
        predictions.append(pred)
    return predictions


def http_replay(docs, settings, seed, recorded=None):
    from fastapi.testclient import TestClient
    from session_pii.app import create_app
    predictions = []
    for doc in docs:
        # Runtime получает только текст, session ID и seed. Ни owners, ни gold
        # entities, ни family сюда не передаются.
        with TestClient(create_app(detector_settings=settings,
                ner=recorded, regexp=EmptyDetector() if recorded is not None else None,
                linker=RandomLinker(session_seed(seed,doc['session_id'])))) as client:
            response = client.post('/v1/sessions',json={'session_id':doc['session_id']})
            response.raise_for_status()
            state, entities, error = response.json(), [], None
            for i,m in enumerate(doc['messages'],1):
                response = client.post('/v1/analyze',json=dict(session_id=state['session_id'],
                    session_epoch=state['session_epoch'],expected_state_version=state['state_version'],
                    message=dict(message_id=state['next_message_id'],role='user',text=m['text'])))
                if response.status_code!=200:
                    assert response.status_code==502
                    error=dict(message_index=i,http_status=response.status_code)
                    break
                state = response.json()
                entities.extend(dict(message_id=m['id'],start=e['start'],end=e['end'],type=e['type'],person_id=e['person_id'])
                                for e in state['entities'])
            predictions.append(dict(entities=entities,first_alert=state.get('verdict',{}).get('first_alert_message_id'),runtime_error=error))
    return predictions


def evaluate(directory, seeds, settings, reuse=False):
    docs = read(directory/'corrected-gold.json')
    snapshot = read(directory/'snapshot.json')
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError('Нужны неповторяющиеся seed')
    assert len(docs) == len(snapshot['records']) == 250
    assert len({d['session_id'] for d in docs}) == len(docs)
    for doc,item in zip(docs,snapshot['records']):
        assert doc['session_id'] == item['session_id'] and doc['messages'] == item['gold']['messages']
        validate(doc)
        texts = {m['id']:m['text'] for m in doc['messages']}
        checks = {'individual_inn':valid_inn,'snils':valid_snils,'bank_card':valid_luhn}
        assert all(checks[e['type']](texts[e['message_id']][e['start']:e['end']])
                   for e in doc['entities'] if e['type'] in checks)
    families = [r['family'] for r in snapshot['records']]
    calculator = Calculator()
    assessments = [assess(d,calculator) for d in docs]
    expected = [a['first_alert']['message_index'] if a['first_alert'] else None for a in assessments]
    write(directory/'corrected-assessments.json',assessments)
    gold_linked = isolated_linker(docs,seeds[0])
    risk = [run_pipeline(d,f,seeds[0],settings,True) for d,f in zip(docs,families)]
    # Сохраняется реальный выход детектора. Runtime и HTTP получают те же
    # неизменяемые спаны, а не эталон. Повторно запускать NER для seed не нужно.
    raw_detector, recordings = record_detector(docs,settings,directory,reuse)
    raw = []
    for doc in docs:
        raw.append([dict(message_id=m['id'],start=e.start,end=e.end,type=e.type)
            for i,m in enumerate(doc['messages'],1)
            for e in raw_detector.detect(Message(message_id=i,text=m['text'])).spans])
    runs = []
    for seed in seeds:
        rows = [run_pipeline(d,f,seed,settings,recorded=raw_detector) for d,f in zip(docs,families)]
        write(directory/f'predictions-seed-{seed}.json',rows)
        successful=[i for i,r in enumerate(rows) if r['runtime_error'] is None]
        latencies = sorted(x for r in rows for x in r['latency_ms'])
        gold_span_rows = [run_pipeline(d,f,seed,settings,reference_spans=True) for d,f in zip(docs,families)]
        runs.append(dict(seed=seed,**alerts([expected[i] for i in successful],[rows[i]['first_alert'] for i in successful]),
            evaluated_sessions=len(successful),runtime_failed_sessions=len(rows)-len(successful),
            metric_scope='completed sessions only; runtime failures excluded with coverage, not silently negative',
            runtime_failures=[dict(session_id=docs[i]['session_id'],**r['runtime_error']) for i,r in enumerate(rows) if r['runtime_error']],
            extraction=extraction([docs[i] for i in successful],[rows[i]['entities'] for i in successful]),
            linker_isolated=pairwise(docs,isolated_linker(docs,seed)),
            gold_spans_random_linker=alerts(expected,[r['first_alert'] for r in gold_span_rows]),
            latency_ms=dict(median=statistics.median(latencies),p95=latencies[max(0,math.ceil(.95*len(latencies))-1)],
                            scope='in-process pipeline with recorded detector outputs; inference reported separately; no HTTP'),
            incomplete_sessions=sum(not r['coverage_complete'] for r in rows)))
    gemma = [r['prediction']['entities'] for r in snapshot['records']]
    http = http_replay(docs,settings,seeds[0],recorded=raw_detector)
    first_run = read(directory/f'predictions-seed-{seeds[0]}.json')
    for a,b in zip(http,first_run):
        assert a['entities']==b['entities'] and a['first_alert']==b['first_alert']
        assert bool(a['runtime_error'])==bool(b['runtime_error'])
        if a['runtime_error']:
            assert a['runtime_error']['message_index']==b['runtime_error']['message_index']
    write(directory/'http-predictions.json',http)
    write(directory/'linker-isolated-predictions.json',gold_linked)
    write(directory/'risk-isolated-predictions.json',risk)
    result = dict(sessions=len(docs),messages=sum(len(d['messages']) for d in docs),positive_sessions=sum(g is not None for g in expected),
        output_directory=directory.as_posix(),
        reference_status='assistant-authored reference reused; template/disagreement review; human review pending',
        detector_mode=settings.mode,model_dir=str(settings.model_dir) if settings.model_dir else None,
        random_policy='uniform n+2 outcomes; one per existing person, NEW, UNKNOWN; no joint owners or service classification; reset per session',
        input_sha256=hashlib.sha256((directory/'corrected-gold.json').read_bytes()).hexdigest(),
        gemma_extraction=extraction(docs,gemma),raw_detector_extraction=extraction(docs,raw),
        linker_isolated=pairwise(docs,gold_linked),
        http_verified_sessions=len(http),
        http_runtime_failures=sum(bool(r['runtime_error']) for r in http),
        inference_reused_from_recordings=reuse,
        detector_inference=dict(unique_texts=len(recordings), total_message_occurrences=sum(len(d['messages']) for d in docs),
            first_call_ms=recordings[0]['latency_ms'],
            median_warm_ms=statistics.median(r['latency_ms'] for r in recordings[1:]),
            p95_warm_ms=sorted(r['latency_ms'] for r in recordings[1:])[max(0,math.ceil(.95*(len(recordings)-1))-1)],
            policy='one real inference per unique exact text; no gold; recorded output reused across seeds and HTTP'),
        risk_isolated=alerts(expected,[r['first_alert'] for r in risk]),runs=runs,
        risk_policy='runtime-risk-v2; independent gold uses annotation-estimator-policy.json; no fitting',
        component_scope='extraction includes adapter and service overlap filters; isolated linker receives gold spans; isolated risk receives gold spans and owners',
        limitations=['completed-response selection bias','synthetic template corpus','not held-out: author created reference',
                    'shared ownership impossible for random baseline','no HTTP or network latency measurement'])
    result['risk_disagreements'] = [dict(session_id=d['session_id'],family=f,gold=g,predicted=r['first_alert'])
        for d,f,g,r in zip(docs,families,expected,risk) if g!=r['first_alert']]
    result['config_sha256'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [ROOT/'configs/runtime-risk-policy.json',ROOT/'configs/annotation-estimator-policy.json',settings.config_path]}
    result['mean_f2_timely'] = statistics.mean(r['timely']['f2'] for r in runs)
    result['mean_linker_f1'] = statistics.mean(r['linker_isolated']['f1'] for r in runs)
    write(directory/'evaluation.json',result)
    (directory/'report.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('sessions','messages','positive_sessions','detector_mode',
        'http_verified_sessions','mean_f2_timely','mean_linker_f1','risk_disagreements')},ensure_ascii=False))
    print(json.dumps([dict(seed=r['seed'],session=r['session'],timely=r['timely'],extraction=r['extraction']['micro']) for r in runs],ensure_ascii=False))


def markdown(result):
    def f(value): return 'N/A' if value is None else f'{value:.4f}'
    directory=result.get('output_directory','data/experiments/random-linker-250-'+
        ('gliner-' if result['detector_mode']=='gliner' else '')+'2026-11-24')
    executable=r'.\.venv-gliner\Scripts\python.exe' if result['detector_mode']=='gliner' else r'..\..\.venv-service\Scripts\python.exe'
    command=f"& '{executable}' -X utf8 tools/evaluate_random_linker.py --directory {directory} --seeds 0 1 2 3 4"
    if result['detector_mode']=='gliner':
        command+=f" --mode gliner --model-dir {result['model_dir']}"
    lines=['# Случайный линкер: фиксированный срез Gemma','',
        f"Сессий: {result['sessions']}; сообщений: {result['messages']}; положительных сессий: {result['positive_sessions']}.",
        'Источник — первые по ID завершённые сессии сохранённого снимка. '
        'Эталон — исходная авторская разметка, повторно использованная по просьбе пользователя. '
        'Проверены шаблоны семейств и расхождения с Gemma; независимой человеческой проверки нет. '
        'Роли неоднозначных вымышленных имён сохранены по авторскому сценарию, журнал — review-ledger.json.',
        '',f"Детектор: `{result['detector_mode']}`. " + (
        'Использованы реальные локальные веса GLiNER и regex. Выход детектора записан один раз '
        'для каждого уникального текста; повторные seed и HTTP используют эти выходы без gold.'
        if result['detector_mode']=='gliner' else
        'Regex-режим помечает покрытие NER как неполное; модель GLiNER в этом запуске не использовалась.'),
        '', 'Исходы линкера: каждый из n известных людей, новый человек и неизвестный владелец — '
        'равновероятны (1/(n+2)). Созданный человек сразу доступен следующим кандидатам. '
        'Seed детерминирован из общего seed и session_id; потоки сессий различаются. '
        'Совместная принадлежность и служебные контакты этим baseline не определяются.',
        '', '## Компоненты','', '| Компонент | Precision | Recall | F2 / F1 |', '|---|---:|---:|---:|']
    for title,row,metric in [('Адаптер до фильтров',result['raw_detector_extraction']['micro'],'f2'),
        ('Извлечение после фильтров, успешные сессии',result['runs'][0]['extraction']['micro'],'f2'),
        ('Сохранённая Gemma, отдельно от runtime',result['gemma_extraction']['micro'],'f2'),
        ('Случайный линкер, правильные спаны, seed=0',result['linker_isolated'],'f1'),
        ('Риск, правильные спаны и владельцы',result['risk_isolated']['timely'],'f2')]:
        lines.append(f"| {title} | {f(row['precision'])} | {f(row['recall'])} | {f(row[metric])} |")
    lines += ['', 'Линкер: pairwise F1 на конечном состоянии сессии по протоколу docs/kb/metrics.md; '
        f"исключено упоминаний с неизвестным эталонным владельцем: {result['linker_isolated']['excluded_gold_unknown_mentions']}. "
        'ID людей между эталоном и предсказанием не сравниваются напрямую. '
        'Совместные владельцы учитываются через пересечение множеств.',
        '', 'Изолированный риск проверяет политику алерта, а не обоснованность численного скора. '
        'CPE Match не рассчитывается: протокол численного эталона не определён.',
        '', '## Весь сервис','', 'Метрики ниже условны на полностью обработанных сессиях. '
        'Ошибки runtime показаны отдельно; они не считаются безопасными отрицательными сессиями. '
        'При наличии ошибок это не полный результат качества сервиса на всём срезе.',
        '', '| Seed | Обработано | Ошибки | TP | FP | FN | TN | F2-session | F2-timely | FP0 | Линкер F1 | F2 при правильных спанах |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in result['runs']:
        c=r['session']
        lines.append(f"| {r['seed']} | {r.get('evaluated_sessions',result['sessions'])} | {r.get('runtime_failed_sessions',0)} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} | {f(c['f2'])} | {f(r['timely']['f2'])} | {f(r['fp0'])} | {f(r['linker_isolated']['f1'])} | {f(r['gold_spans_random_linker']['timely']['f2'])} |")
    lines += ['',f"Среднее F2-timely по seed: {f(result['mean_f2_timely'])}; среднее pairwise F1: {f(result['mean_linker_f1'])}. "
        'Это средние повторных запусков на одних данных, не macro по группам и не доверительный интервал.',
        f"HTTP-прогон seed=0: {result['http_verified_sessions']} сессий; ошибки: {result.get('http_runtime_failures',0)}. "
        'Спаны, владельцы, первый алерт и позиции ошибок совпали с прямым Pipeline. '
        'Runtime не получает эталонные сущности, владельцев и family. Эталон передаётся только в явно изолированные проверки.',
        '', '## Задержка','', 'Pipeline ниже использует записанные выходы детектора. '
        'Инференс и первоначальная загрузка модели измерены отдельно, без HTTP/сети. Это не нагрузочный тест.',
        f"Уникальных текстов с реальным вызовом детектора: {result['detector_inference']['unique_texts']}. "
        f"Первый вызов с инициализацией: {f(result['detector_inference']['first_call_ms'])} мс; "
        f"медиана следующих вызовов: {f(result['detector_inference']['median_warm_ms'])} мс; "
        f"p95: {f(result['detector_inference']['p95_warm_ms'])} мс.",
        '', '| Seed | Медиана, мс | p95, мс |', '|---|---:|---:|']
    lines += [f"| {r['seed']} | {f(r['latency_ms']['median'])} | {f(r['latency_ms']['p95'])} |" for r in result['runs']]
    lines += ['', '## Ошибки и ограничения','',
        'Разрезы извлечения по типам — в evaluation.json. Пропуски и лишние спаны '
        'учитываются отдельно от ошибок принадлежности.',
        'Восемь изолированных ложных алертов риска относятся к unknown_contact_pair: '
        'телефон и почта имеют person_id=null, а их веса 0.3 + 0.3 превышают unknown_threshold=0.5. '
        'Эталонная политика не связывает эти факты с одним человеком и алерта не требует. '
        'Веса и эталон в эксперименте не менялись.',
        'Примеры: synthetic-1000-0046 (неизвестные контакты); synthetic-1000-0011 '
        '(пропуск паспорта); synthetic-1000-0007 (ФИ, общий адрес); synthetic-1000-0053 '
        '(части ФИО в разных сообщениях). Сохранённые предсказания позволяют разобрать все ошибки.',
        'Высокая точность контактов на шаблонной синтетике не переносится автоматически на реальные диалоги. '
        'Выбраны только завершённые ответы Gemma, поэтому возможен отбор по успешности запроса. '
        'Gemma не является NER-компонентом измеренной runtime-цепочки.',
        '', '## Воспроизведение','', 'Из корня worktree:', '', '```powershell',
        command,
        '```', '', 'Команда повторяет расчёт и HTTP-прогон на фиксированном эталоне, без внешних API. '
        'Метрики воспроизводятся; замеры времени зависят от машины. Для нового набора сначала '
        'сохранить snapshot.json и однократно передать --prepare. Исходные прогоны Gemma не изменяются.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--seeds',type=int,nargs='+',default=[0,1,2,3,4])
    parser.add_argument('--mode',choices=['regex','gliner'],default='regex')
    parser.add_argument('--model-dir',type=Path)
    parser.add_argument('--reuse-recordings',action='store_true',help='Пересчитать сохранённые выходы этого запуска без нового инференса')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.directory)
    evaluate(args.directory,args.seeds,DetectorSettings(mode=args.mode,model_dir=args.model_dir),args.reuse_recordings)
