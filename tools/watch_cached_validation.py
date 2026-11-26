"""Независимый локальный отчёт о validation; модели и API не вызывает."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def snapshot(run):
    status = read(run/'status.json', {})
    source = read(run/'input-snapshot.json', {'records': []})
    records = source['records']
    session_ids = {row['session_id'] for row in records}
    predictions = {}
    for row in records:
        result = read(run/'predictions'/(row['session_id']+'.json'))
        if result and result.get('status') == 'complete':
            predictions[row['session_id']] = result
    failures = []
    for path in sorted((run/'failures').glob('*.json')):
        value = read(path)
        if value and value['session_id'] in session_ids and value['session_id'] not in predictions:
            failures.append(value)
    try:
        pid = int(run.with_name(run.name+'-worker.pid').read_text().strip())
    except (OSError, ValueError):
        pid = None
    result = dict(updated_at=datetime.now(timezone.utc).isoformat(), worker_pid=pid,
                  worker_alive=alive(pid), worker_status=status, total=len(records),
                  completed=len(predictions), unresolved_failures=len(failures),
                  failure_reasons=dict(Counter(f.get('error', f.get('error_type', '?')) for f in failures)),
                  pending=len(records)-len(predictions)-len(failures),
                  cache={name: len(list((run/directory).glob('*.json'))) for name, directory in
                         [('ner', 'ner/by-text'), ('llm', 'llm-cache'),
                          ('rejected_llm', 'rejected-llm-cache'), ('transport_errors', 'transport-errors')]},
                  ner_extraction=read(run/'ner/extraction.json'),
                  calibration=read(run/'calibration/report.json'))
    if predictions:
        from calibrate_cached_risk import measured
        from evaluate_gemma_linker import matched_linker_metrics
        from evaluate_random_linker import extraction
        selected = [r for r in records if r['session_id'] in predictions]
        docs = [r['gold'] for r in selected]
        values = [predictions[r['session_id']] for r in selected]
        rows = [dict(expected=r['expected_first_alert'],
                     uncertain=r.get('final_uncertain',
                         r['gold'].get('case_flags', {}).get('annotation_uncertain', False))) for r in selected]
        result['partial_metrics'] = dict(
            alerts=measured(rows, [p['first_alert'] for p in values]),
            extraction=extraction(docs, [p['entities'] for p in values]),
            linker=matched_linker_metrics(docs, values))
    return result


def number(value):
    return f'{value:.4f}' if isinstance(value, (int, float)) else '—'


def render(data, run, interval):
    status = data['worker_status']
    lines = ['# Онлайн-прогресс validation и подбора риска', '',
             f"Обновлено (UTC): {data['updated_at']}. Интервал: {interval} с.", '',
             f"Процесс: {'работает' if data['worker_alive'] else 'не обнаружен — прогон мог остановиться или завершиться'}; PID: {data['worker_pid']}. Этап: {status.get('stage', 'неизвестен')}.",
             f"Последнее событие worker (UTC): {status.get('updated_at', '—')}.", '',
             f"Полностью завершено: **{data['completed']}/{data['total']}**; незавершённых с сохранённой ошибкой: {data['unresolved_failures']}; остальных: {data['pending']}.",
             f"NER: {data['cache']['ner']}/{status.get('ner_total', '?')} уникальных текстов; кэш ответов линкера: {data['cache']['llm']}.",
             f"Отвергнутых ответов в архиве: {data['cache']['rejected_llm']}; записей транспортных ошибок: {data['cache']['transport_errors']}.", '',
             'Числа ниже предварительные: только полностью завершённые сессии; ошибки не считаются TN. Состав подмножества меняется, сравнивать последовательные обновления как улучшение модели нельзя.', '',
             '| Метрика | Precision | Recall | F2 |', '|---|---:|---:|---:|']
    metrics = data.get('partial_metrics', {})
    for name, values in [('Алерт: сессия', metrics.get('alerts', {}).get('session', {})),
                         ('Алерт: своевременно', metrics.get('alerts', {}).get('timely', {})),
                         ('Экстракция после обработки сервисом', metrics.get('extraction', {}).get('micro', {}))]:
        lines.append('| '+name+' | '+' | '.join(number(values.get(k)) for k in ('precision', 'recall', 'f2'))+' |')
    linker = metrics.get('linker', {})
    lines += ['', f"Линкер на точно совпавших спанах: precision={number(linker.get('precision'))}, recall={number(linker.get('recall'))}, F1={number(linker.get('f1'))}; совпало {linker.get('matched_mentions', 0)}/{linker.get('gold_mentions_in_completed_sessions', 0)} эталонных упоминаний.",
              f"FP0 по уверенно отрицательным: {number(metrics.get('alerts', {}).get('fp0'))}; знаменатель: {metrics.get('alerts', {}).get('fp0_denominator', 0)}.",
              'Метрики линкера условны на совпавших спанах и не включают ошибки экстракции.']
    lines += ['', 'Метрики линкера, FP0 и исходы ошибок доступны в [online-summary.json](online-summary.json).',
              'Отдельный результат NER до фильтрации сервисом: [ner/extraction.json](ner/extraction.json).', '',
              '## Незавершённые сессии с ошибками', '']
    lines += [f'- {reason}: {count}' for reason, count in data['failure_reasons'].items()] or ['Нет сохранённых ошибок незавершённых сессий.']
    report = data.get('calibration')
    lines += ['', '## Подбор весов и порогов', '']
    if report:
        lines += [f"Покрытие: {report['sessions']}/{report.get('source_sessions', data['total'])}. Предварительный подбор: {report.get('provisional', False)}.",
                  f"Выбранный вариант на validation: `{report['recommended_variant']}`.", '',
                  '| Вариант | F2-session | F2-timely | Δ F2-timely, п.п. |', '|---|---:|---:|---:|']
        for name, row in report['results'].items():
            lines.append(f"| {name} | {number(row['validation']['session']['f2'])} | {number(row['validation']['timely']['f2'])} | {number(row['delta_pp']['timely']['f2'])} |")
        lines += ['', 'Параметры и их дельты: [calibration/report.md](calibration/report.md), [полный JSON](calibration/report.json).']
    else:
        lines += ['Результата ещё нет. Подбор запустится после прохода сессий. При ошибках требуется не менее 90% покрытия; такой подбор помечается предварительным и может быть смещён исключением ошибок.']
    lines += ['', 'Меняем веса атрибутов и пороги риска для известных лиц и неизвестного владельца. Порог GLiNER остаётся 0.5. Кэш NER и линкера используется повторно; test не запускается, конфигурация сервиса не меняется.', '',
              'Документ обновляется отдельным локальным процессом. Если время обновления перестало меняться, остановился наблюдатель. IDE должна перечитать файл с диска.', '',
              'Воспроизвести текущее состояние:', '', '```powershell',
              f'.venv-gliner/Scripts/python.exe -X utf8 tools/watch_cached_validation.py --run data/experiments/{run.name} --once', '```', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('--interval must be positive')
    args.run.mkdir(parents=True, exist_ok=True)
    while True:
        data = snapshot(args.run)
        for filename, content in [('online-summary.json', json.dumps(data, ensure_ascii=False, indent=2)),
                                  ('online-progress.md', render(data, args.run, args.interval))]:
            target = args.run/filename
            temporary = target.with_suffix(target.suffix+'.watch.tmp')
            temporary.write_text(content+'\n', encoding='utf-8')
            temporary.replace(target)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
