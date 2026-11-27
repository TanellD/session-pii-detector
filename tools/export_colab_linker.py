"""Подготовить Colab notebook и переносимый архив синтетического среза линкера."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
from annotation_calculator import Calculator
from annotation_contract import assess, validate


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def notebook():
    cells = []
    def cell(kind, source):
        row = dict(id=f'cell-{len(cells):02d}', cell_type=kind, metadata={}, source=textwrap.dedent(source).strip() + '\n')
        if kind == 'code':
            row.update(execution_count=None, outputs=[])
        cells.append(row)
    cell('markdown', '''
    # Qwen3-4B: линкер владельцев на наших синтетических сессиях

    В Colab выберите **Runtime → Change runtime type → GPU**. Модель загружается
    с Hugging Face и работает на GPU Colab, без Radeon API и ключа Gemma.
    По умолчанию Qwen3-4B, 4-bit NF4, reasoning выключен. Можно выбрать Qwen3-1.7B.
    Доступность GPU и точное потребление памяти проверяются при запуске.

    Загрузите подготовленный `qwen-linker-colab-bundle.zip` в ячейке ниже.
    Вход содержит тексты и эталонные спаны, **без владельцев**; эталон используется
    только оценщиком. Это оценка линкера, а не полного NER-сервиса.
    ''')
    cell('code', '''
    import subprocess, sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q',
        'transformers==4.57.6', 'accelerate>=1.2,<2', 'bitsandbytes>=0.48,<0.49',
        'pydantic>=2.10,<3', 'sentencepiece>=0.2,<0.3'])
    ''')
    cell('code', '''
    from google.colab import files
    from pathlib import Path
    import zipfile, os, hashlib, json
    uploaded = files.upload()  # Выберите qwen-linker-colab-bundle.zip
    archive = next(Path(name) for name in uploaded if name.endswith('.zip'))
    ROOT = Path('/content/session-pii-colab')
    ROOT.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (ROOT / name).resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('Недопустимый путь внутри архива')
        z.extractall(ROOT)
    os.chdir(ROOT)
    manifest = json.loads((ROOT/'bundle-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
    print('Проверено файлов:', len(manifest['files']))
    print('Сессий:', manifest['sessions'])
    ''')
    cell('markdown', '''
    ## Где сохранять результаты

    Для сохранения после отключения Colab поставьте `USE_DRIVE=True` и разрешите
    подключение своего Drive. Иначе файлы остаются только во временном `/content`;
    скачивайте их последней ячейкой до завершения runtime. При продолжении используйте
    прежнюю папку. Для другой модели или параметров задайте новое имя запуска.
    ''')
    cell('code', '''
    USE_DRIVE = False
    MODEL_ID = 'Qwen/Qwen3-4B'  # Альтернатива: Qwen/Qwen3-1.7B
    RUN_NAME = MODEL_ID.split('/')[-1] + '-nf4-no-thinking-owner-v2'
    MAX_NEW_TOKENS = 1024
    MAX_INPUT_TOKENS = 8192
    if USE_DRIVE:
        from google.colab import drive
        drive.mount('/content/drive')
        OUTPUT = Path('/content/drive/MyDrive/session-pii-linker') / RUN_NAME
    else:
        OUTPUT = ROOT / 'results' / RUN_NAME
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print('Результаты:', OUTPUT)
    ''')
    cell('code', '''
    import torch, importlib.metadata
    from huggingface_hub import HfApi
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    assert torch.cuda.is_available(), 'Включите GPU в настройках Colab'
    print(torch.cuda.get_device_name(0))
    saved = OUTPUT / 'manifest.json'
    old = json.loads(saved.read_text())['model'] if saved.exists() else None
    if old and old['model_id'] != MODEL_ID:
        raise ValueError('Для другой модели выберите новую папку результатов')
    REVISION = old['revision'] if old else HfApi().model_info(MODEL_ID).sha
    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=REVISION,
        trust_remote_code=False, device_map={'': 0}, torch_dtype=compute_dtype,
        attn_implementation='sdpa', quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype))
    model.eval()
    sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'src'), str(ROOT)]
    from colab_linker import LocalCompletion, run_sessions, evaluate, write, LOCAL_OWNER_GUIDANCE
    completion = LocalCompletion(model, tokenizer, MAX_INPUT_TOKENS)
    identity = dict(model_id=MODEL_ID, revision=REVISION, quantization='nf4-double',
        compute_dtype=str(compute_dtype), thinking=False, do_sample=False,
        max_new_tokens=MAX_NEW_TOKENS, max_input_tokens=MAX_INPUT_TOKENS,
        local_guidance_sha256=hashlib.sha256(LOCAL_OWNER_GUIDANCE.encode()).hexdigest(),
        schema_mode='schema in prompt; strict local validation; no constrained decoding',
        bundle_sha256=hashlib.sha256((ROOT/'bundle-manifest.json').read_bytes()).hexdigest())
    write(OUTPUT/'environment.json', dict(gpu=torch.cuda.get_device_name(0), identity=identity,
        versions={p:importlib.metadata.version(p) for p in
                  ['torch','transformers','accelerate','bitsandbytes','pydantic']}))
    print('Revision:', REVISION)
    print('Выделено GPU, GiB:', round(torch.cuda.memory_allocated()/2**30, 2))
    ''')
    cell('markdown', '''
    ## Пробный запуск

    Первые сессии выполняются с сохранением всех запросов и ответов. В полном запуске
    успешно завершённые сессии повторяться не будут. Неуспешные тоже не повторяются
    автоматически; для явного повтора используйте `retry_failed=True`.
    JSON не исправляется автоматически: обрезанный или неверный ответ считается ошибкой.
    ''')
    cell('code', '''
    state = run_sessions(ROOT/'data/inference.json', OUTPUT, completion, identity, limit=5)
    summary = evaluate(OUTPUT, ROOT/'data/reference.json', ROOT/'data/gemma-baseline.json')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ''')
    cell('code', '''
    # Посмотрите сырой ответ до запуска остальных сессий.
    paths = sorted(OUTPUT.glob('*/attempt-*/call-*-response.json'))
    if paths:
        raw = json.loads(paths[0].read_text())
        print('Первый ответ:', raw['choices'][0]['message']['content'])
    print('Совпавших групп:', summary['quality']['exact_owner_groups'], '/', summary['completed'])
    print('complete означает успешный контракт, а не правильное назначение владельца.')
    ''')
    cell('markdown', '''
    ## Полный прогон и продолжение

    Выполните ячейку после проверки пробных ответов. При остановке запустите её снова:
    сохраняется каждая завершённая сессия. После перезапуска runtime сначала повторите
    установку, загрузку архива и модели с прежним OUTPUT. Незавершённая сессия начнётся
    заново, старая попытка сохранится. Веса модели в архив результатов не включаются.
    ''')
    cell('code', '''
    try:
        state = run_sessions(ROOT/'data/inference.json', OUTPUT, completion, identity)
    finally:
        summary = evaluate(OUTPUT, ROOT/'data/reference.json', ROOT/'data/gemma-baseline.json')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    ''')
    cell('markdown', '''
    Оценщик показывает покрытие и ошибки отдельно от качества успешных ответов:
    pairwise Precision/Recall/F1, точные группы владельцев, Precision/Recall и два F2
    алертов. В `common_subset` — сравнение с Gemma на одинаковых успешных сессиях.
    Неизвестные владельцы исключены из pairwise, но учтены в точных группах.

    Локальное время включает токенизацию и генерацию, исключает загрузку весов;
    время генерации дополнительно записано после CUDA synchronization. Это не RPS-тест.
    NF4 и отсутствие constrained decoding отличают запуск от серверного Gemma.
    Синтетические короткие сценарии не заменяют независимый тест реальных чатов.
    ''')
    cell('code', '''
    import shutil
    archive = shutil.make_archive('/content/' + RUN_NAME + '-results', 'zip', OUTPUT)
    files.download(archive)
    ''')
    return dict(nbformat=4, nbformat_minor=5,
        metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
                      language_info=dict(name='python'), colab=dict(name='qwen3_linker_colab.ipynb'), accelerator='GPU'),
        cells=cells)


def build(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'data'):
        raise ValueError('Экспорт должен находиться в игнорируемом data/')
    output.mkdir(parents=True, exist_ok=True)
    source_path = ROOT / 'data/experiments/random-linker-250-2026-11-24/snapshot.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    calculator = Calculator()
    inference, reference = [], []
    for item in source['records']:
        doc = item['gold']
        if not doc['session_id'].startswith('synthetic-'):
            raise ValueError('Разрешён только синтетический срез')
        validate(doc)
        messages = [dict(id=m['id'], text=m['text'], candidates=[
            dict(type=e['type'], start=e['start'], end=e['end'])
            for e in doc['entities'] if e['message_id']==m['id']]) for m in doc['messages']]
        inference.append(dict(session_id=doc['session_id'], messages=messages))
        alert = assess(doc, calculator)['first_alert']
        reference.append(dict(gold=doc, family=item['family'], first_alert=alert['message_index'] if alert else None))
    baseline_dir = ROOT / 'data/experiments/gemma-linker-250-2026-11-24/results'
    baseline_state = json.loads((baseline_dir/'manifest.json').read_text(encoding='utf-8'))
    if baseline_state['input_sha256'] != hashlib.sha256(source_path.read_bytes()).hexdigest():
        raise ValueError('Gemma baseline относится к другому входу')
    baseline = {r['session_id']:json.loads((baseline_dir/r['path']).read_text(encoding='utf-8'))
                for r in baseline_state['sessions'] if r['status']=='complete'}
    files = {}
    for directory in ['src/session_pii', 'benchmarks/adapter', 'benchmarks/pii_smoke']:
        for path in (ROOT / directory).rglob('*.py'):
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ['colab_linker', 'annotation_contract', 'annotation_calculator',
                 'estimate_occupation_candidates', 'ownership', 'compare_annotation_models', 'evaluate_random_linker']:
        path = ROOT / 'tools' / (name+'.py')
        files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ['runtime-risk-policy.json','annotation-estimator-policy.json','occupation-okz-mapping.json']:
        files['configs/'+name] = (ROOT/'configs'/name).read_bytes()
    files['data/inference.json'] = encoded(dict(schema_version='linker-input-v1', sessions=inference))
    files['data/reference.json'] = encoded(dict(sessions=reference))
    files['data/gemma-baseline.json'] = encoded(dict(model=baseline_state['model'], predictions=baseline))
    files['data/gemma-summary.json'] = (baseline_dir/'summary.json').read_bytes()
    files['docs/kb/metrics.md'] = (ROOT/'docs/kb/metrics.md').read_bytes()
    manifest = dict(sessions=len(inference), base_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        files={name:hashlib.sha256(value).hexdigest() for name,value in files.items()})
    files['bundle-manifest.json'] = encoded(manifest)
    archive = output / 'qwen-linker-colab-bundle.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, value in sorted(files.items()):
            z.writestr(name, value)
    path = ROOT / 'notebooks/qwen3_linker_colab.ipynb'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded(notebook()))
    shutil.copyfile(path, output / path.name)
    # Отдельный вход удобен для своего клиента инференса.
    (output / 'inference.json').write_bytes(files['data/inference.json'])
    (output / 'reference.json').write_bytes(files['data/reference.json'])
    writeup = 'Откройте qwen3_linker_colab.ipynb в Colab, включите GPU, выполните ячейки сверху вниз.\n' \
        'При запросе файла выберите qwen-linker-colab-bundle.zip. Архив содержит только синтетический срез и разрешённые исходники, без ключей и весов.\n' \
        'Владельцы находятся отдельно в reference.json и не передаются модели. Результаты сохраняйте в Drive или скачайте последней ячейкой.\n'
    (output / 'README.txt').write_text(writeup, encoding='utf-8')
    print(json.dumps(dict(archive=str(archive), sessions=len(inference), bytes=archive.stat().st_size,
                         sha256=hashlib.sha256(archive.read_bytes()).hexdigest()), ensure_ascii=False))
    return archive


def verify_archive(archive):
    import tempfile
    check = '''
import sys, hashlib
from pathlib import Path
root=Path(sys.argv[1]); sys.path[:0]=[str(root/'tools'),str(root/'src'),str(root)]
import colab_linker as c
assert Path(c.__file__).is_relative_to(root)
manifest=c.read(root/'bundle-manifest.json')
for name,digest in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest, name
inputs=c.read(root/'data/inference.json')
def check_keys(value):
    if isinstance(value,dict):
        assert not ({'person_id','owners','first_alert','family'} & set(value))
        for row in value.values(): check_keys(row)
    elif isinstance(value,list):
        for row in value: check_keys(row)
check_keys(inputs)
assert len(inputs['sessions'])==manifest['sessions']
baseline=c.read(root/'data/gemma-baseline.json')['predictions']
out=root/'verification'; out.mkdir(); sessions=[]
for sid,pred in baseline.items():
    c.write(out/(sid+'.json'),pred)
    sessions.append(dict(session_id=sid,status='complete',path=sid+'.json'))
c.write(out/'manifest.json',dict(total=manifest['sessions'],sessions=sessions))
summary=c.evaluate(out,root/'data/reference.json',root/'data/gemma-baseline.json')
expected=c.read(root/'data/gemma-summary.json')
assert summary['completed']==expected['completed_sessions']
assert summary['quality']['exact_owner_groups']==expected['owner_groups_exact_sessions']
assert summary['quality']['pairwise']==expected['pairwise']
assert summary['quality']['alerts']==expected['runtime_alerts']
print('Portable imports, file hashes, owner-free inputs and Gemma metric replay: OK')
'''
    (ROOT/'data/tmp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='colab-verify-', dir=ROOT/'data/tmp') as directory:
        with zipfile.ZipFile(archive) as z:
            z.extractall(directory)
        subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', check, directory],
                       cwd=directory, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'data/exports/colab-linker-250-2026-11-25-v2')
    parser.add_argument('--verify', action='store_true', help='Проверить архив в изолированном процессе без GPU')
    args=parser.parse_args()
    archive=build(args.output)
    if args.verify:
        verify_archive(archive)
