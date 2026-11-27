"""Локальный HF-инференс линкера и возобновляемая оценка экспортированного среза."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT)]
from session_pii.components import DetectionResult, EmptyDetector
from session_pii.gemma_linker import GemmaLinker, GemmaSettings, PROMPT
from session_pii.models import Message, Span, Verdict
from session_pii.pipeline import Pipeline
from evaluate_random_linker import pairwise, alerts
from compare_annotation_models import groups

LOCAL_OWNER_GUIDANCE = '''
Под владельцем понимается человек, О КОТОРОМ сообщаются сведения, а не тот,
кто знает сведения, произносит сообщение или имеет доступ к ним.
known_people содержит только профили, созданные ранее. Пустой known_people
не означает, что владелец неизвестен: если в тексте явно указан новый человек,
создай new:N. Имя и фамилия одного нового человека получают ОДИН new:N.
Личность не обязана быть названа: «Мне ... лет» и «мой пол ...» относятся
к говорящему; если его профиля ещё нет, создай для него new:N.
owners=[] допустим, когда из текста и истории нельзя установить, к какому
человеку относятся сведения, либо для явно служебного контакта.
Не создавай нового человека для каждого атрибута одного и того же лица.
Если известный человек продолжает говорить о себе, используй его существующий ID.
Это правила назначения владельцев, а не требование угадывать при неоднозначности.
'''.strip()


def local_messages(body):
    messages = [dict(row) for row in body['messages']]
    schema = body['response_format']['json_schema']['schema']
    messages[0]['content'] += '\n' + LOCAL_OWNER_GUIDANCE
    messages[0]['content'] += '\nСхема итогового JSON:\n' + json.dumps(schema, ensure_ascii=False)
    return messages


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


class LocalCompletion:
    """Transformers generate без внешнего LLM API; строгий JSON проверяет GemmaLinker."""
    def __init__(self, model, tokenizer, max_input_tokens=8192):
        self.model, self.tokenizer = model, tokenizer
        self.max_input_tokens = max_input_tokens

    def __call__(self, body):
        import torch
        # HF generate не применяет response_format: схему явно даём в промпте.
        messages = local_messages(body)
        text = self.tokenizer.apply_chat_template(messages, tokenize=False,
                    add_generation_prompt=True, enable_thinking=False)
        inputs = self.tokenizer(text, return_tensors='pt').to(self.model.device)
        n = inputs['input_ids'].shape[1]
        if n > self.max_input_tokens:
            raise ValueError('Input context limit exceeded; input was not truncated')
        torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=body['max_tokens'],
                do_sample=False, pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        generated = output[0, n:]
        content = self.tokenizer.decode(generated, skip_special_tokens=True)
        eos = self.model.generation_config.eos_token_id
        eos = set(eos if isinstance(eos, list) else [eos])
        stopped = bool(len(generated)) and int(generated[-1]) in eos
        return dict(choices=[dict(finish_reason='stop' if stopped else 'length',
                    message=dict(content=content))],
                    effective_messages=messages,
                    local_timing=dict(generation_seconds=elapsed, input_tokens=n,
                                      generated_tokens=len(generated)))


class InputDetector:
    def __init__(self, messages):
        self.messages = messages

    def detect(self, message):
        row = self.messages[message.message_id - 1]
        return DetectionResult(tuple(Span(type=e['type'], start=e['start'], end=e['end'])
                                     for e in row['candidates']))


def run_sessions(input_path, output, complete, model_identity, limit=None, retry_failed=False):
    """Не читает reference.json: инференс получает только inference.json."""
    input_path, output = Path(input_path), Path(output)
    source = read(input_path)
    output.mkdir(parents=True, exist_ok=True)
    identity = dict(input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
        prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
        local_guidance_sha256=hashlib.sha256(LOCAL_OWNER_GUIDANCE.encode()).hexdigest(), model=model_identity)
    manifest = output / 'manifest.json'
    state = read(manifest) if manifest.exists() else dict(**identity, sessions=[], total=len(source['sessions']))
    if any(state.get(k) != v for k, v in identity.items()):
        raise ValueError('Изменились модель, данные или промпт: используйте новый каталог результатов')
    selected = source['sessions'] if limit is None else source['sessions'][:limit]
    settings = GemmaSettings(api_key='local-no-api', model=model_identity['model_id'],
        base_url='http://localhost', context_messages=3, max_tokens=model_identity['max_new_tokens'])
    for doc in selected:
        sid = doc['session_id']
        attempts = [r for r in state['sessions'] if r['session_id'] == sid]
        if any(r['status'] == 'complete' for r in attempts):
            continue
        if attempts and attempts[-1]['status'] == 'failed' and not retry_failed:
            continue
        attempt = output / sid / f'attempt-{len(attempts)+1:02d}'
        attempt.mkdir(parents=True)
        record = dict(session_id=sid, status='running', attempt=len(attempts)+1)
        state['sessions'].append(record)
        write(manifest, state)
        calls = 0
        def request(body):
            nonlocal calls
            calls += 1
            write(attempt / f'call-{calls:02d}-request.json', body)
            start = time.perf_counter()
            result = complete(body)
            result['local_roundtrip_seconds'] = time.perf_counter() - start
            write(attempt / f'call-{calls:02d}-response.json', result)
            return result
        pipe = Pipeline(InputDetector(doc['messages']), EmptyDetector(), GemmaLinker(settings, complete=request))
        facts, verdict, history, entities = (), Verdict(), (), []
        try:
            for i, row in enumerate(doc['messages'], 1):
                message = Message(message_id=i, text=row['text'])
                step = pipe.process(message, facts, verdict, session_id=sid, previous_messages=history)
                facts, verdict = step.facts, step.verdict
                history = (history + (message,))[-3:]
                entities.extend(dict(message_id=row['id'], type=e.type, start=e.start, end=e.end,
                    person_id=e.person_id, service_contact=e.service_contact) for e in step.entities)
            path = attempt / 'prediction.json'
            write(path, dict(session_id=sid, entities=entities, first_alert=verdict.first_alert_message_id))
            record.update(status='complete', path=path.relative_to(output).as_posix())
        except Exception as exc:
            record.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:300])
            # OOM не маскируем как ещё одну неверную атрибуцию.
            if type(exc).__name__ == 'OutOfMemoryError':
                write(manifest, state)
                raise
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        write(manifest, state)
        done = sum(r['status'] == 'complete' for r in state['sessions'])
        print(f'{sid}: {record["status"]}; успешно {done}/{state["total"]}', flush=True)
    return state


def evaluate(output, reference_path, baseline_path=None):
    """Эталон используется после инференса, не при формировании промптов."""
    output = Path(output)
    state = read(output / 'manifest.json')
    refs = {r['gold']['session_id']:r for r in read(reference_path)['sessions']}
    predictions = {r['session_id']:read(output / r['path'])
                   for r in state['sessions'] if r['status'] == 'complete'}
    def metrics(ids, preds):
        gold = [refs[sid]['gold'] for sid in ids]
        return dict(sessions=len(ids), exact_owner_groups=sum(groups(g)==groups(preds[sid])
            for g,sid in zip(gold,ids)), pairwise=pairwise(gold, [preds[sid]['entities'] for sid in ids]),
            alerts=alerts([refs[sid]['first_alert'] for sid in ids], [preds[sid]['first_alert'] for sid in ids]))
    result = dict(total=state['total'], completed=len(predictions),
        failures=[r for r in state['sessions'] if r['status']=='failed'],
        quality=metrics(sorted(predictions), predictions))
    timings = [read(p) for p in output.glob('*/attempt-*/call-*-response.json')]
    import statistics
    values = [r['local_roundtrip_seconds'] for r in timings]
    result['timing'] = dict(calls=len(values), median_roundtrip_seconds=statistics.median(values) if values else None,
                           max_roundtrip_seconds=max(values) if values else None,
                           includes_failed_attempts=True, model_load_excluded=True)
    if baseline_path:
        baseline = read(baseline_path)['predictions']
        common = sorted(set(predictions) & set(baseline))
        result['common_subset'] = dict(local=metrics(common,predictions), gemma=metrics(common,baseline))
    write(output / 'summary.json', result)
    return result
