"""Обновляемый Markdown-отчёт сохранённого прогона; API не вызывается."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

from evaluate_gemma_linker import ROOT, summarize


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def render(run):
    output = run / 'results'
    if not (output / 'manifest.json').exists() and (run / 'preparation.json').exists():
        prep = json.loads((run / 'preparation.json').read_text(encoding='utf-8'))
        pid = int((run / 'worker.pid').read_text().strip())
        active = alive(pid)
        log = run / 'logs/worker-stdout.log'
        matches = re.findall(r'Inference completed: (\d+) unique texts',
                             log.read_text(encoding='utf-8') if log.exists() else '')
        count = int(matches[-1]) if matches else 0
        text = '\n'.join(['# Сложные диалоги — NER v2 и Gemma', '',
            f'Обновлено: {datetime.now(timezone.utc).isoformat(timespec="seconds")} (UTC).', '',
            '**Подготовка реального NER**' if active else '**Процесс остановлен до запуска Gemma**', '',
            f'Сессий: {prep["total_sessions"]}; сообщений: {prep["total_messages"]}.', '',
            f'GLiNER: не менее {count}/{prep["unique_texts"]} уникальных текстов по журналу.', '',
            'После сохранения NER автоматически запускается Pipeline с ревизией v2 и живым Gemma API.', '',
            'Метрики полного сервиса пока отсутствуют. Строки прогресса NER появляются каждые 25 текстов.', '',
            '[Журнал NER](logs/worker-stdout.log), [этап](stage.json).', '',
            'Повторное обновление без API:', '', '```powershell',
            f'.\\.venv-gliner\\Scripts\\python.exe -X utf8 tools/watch_gemma_linker.py --run {run.relative_to(ROOT).as_posix()} --once',
            '```', ''])
        temporary = run / 'live-progress.tmp'
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(run / 'live-progress.md')
        return not active
    input_path=output/'input-snapshot.json'
    if not input_path.exists():
        input_path=ROOT / 'data/experiments/random-linker-250-2026-11-24/snapshot.json'
    source = json.loads(input_path.read_text(encoding='utf-8'))
    report = summarize(output, source)
    state = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    pid = int((run / 'worker.pid').read_text().strip())
    active = alive(pid)
    done, total = report['completed_sessions'], report['selected_sessions']
    latest={row['session_id']:row for row in state['sessions']}
    processed=sum(row['status'] in {'complete','failed'} for row in latest.values())
    status = 'Завершён' if done == total else ('Выполняется' if active else
        'Обход завершён; есть ошибки' if processed == total else 'Остановлен: требуется продолжение')
    last = state['sessions'][-1] if state['sessions'] else {}
    percent = lambda value: 'N/A' if value is None else f'{value * 100:.2f}%'
    number = lambda value: 'N/A' if value is None else f'{value:.2f}'
    pair = report['pairwise']
    session = report['runtime_alerts']['session']
    timely = report['runtime_alerts']['timely']
    failed = [row for row in state['sessions'] if row['status'] == 'failed']
    lines = [
        f'# {report["model"]} — live-прогресс линкера', '',
        f'Обновлено: {datetime.now(timezone.utc).isoformat(timespec="seconds")} (UTC).', '',
        f'**{status}. Завершено {done}/{total} ({done / total:.1%}).**', '',
        f'Проверено с учётом ошибок: {processed}/{total}.', '',
        f'Процесс: `{pid}`; последняя попытка: `{last.get("session_id", "—")}`, `{last.get("status", "—")}`.', '',
        'Отчёт обновляется каждые 15 секунд, пока процесс работает. После остановки дата выше показывает свежесть последней проверки.', '',
        '| Показатель | Значение |', '|---|---:|',
        f'| Точное совпадение групп владельцев | {report["owner_groups_exact_sessions"]}/{done} |',
        f'| Pairwise Precision | {percent(pair["precision"])} |',
        f'| Pairwise Recall | {percent(pair["recall"])} |',
        f'| Pairwise F1 | {percent(pair["f1"])} |',
        f'| Precision алертов, session / timely | {percent(session["precision"])} / {percent(timely["precision"])} |',
        f'| Recall алертов, session / timely | {percent(session["recall"])} / {percent(timely["recall"])} |',
        f'| F2-session / F2-timely | {percent(session["f2"])} / {percent(timely["f2"])} |',
        f'| Завершённые вызовы API, включая прежние попытки | {report["api"]["completed_calls"]} |',
        f'| Медиана / максимум времени API, с | {number(report["api"]["p50_seconds"])} / {number(report["api"]["max_seconds"])} |',
        f'| Неуспешные попытки, включая восстановленные | {len(failed)} |', '',
        ('Метрики алертов рассчитаны только по завершённым сессиям. Реальный GLiNER + regex → фильтры сервиса → Gemma → риск. '
         'Pairwise — только на спанах с точным совпадением типа и границ; пропуски и лишние спаны учитываются в извлечении. ' if report.get('detector_mode') else
         'Метрики рассчитаны только по завершённым сессиям. Эталонные спаны → LLM → расчёт риска; ошибки NER не измеряются. ')+
        'Синтетический срез частично использовался при отладке. Значения не доказывают качество на реальных диалогах.', '',
        f'Pairwise: `{json.dumps(pair["counts"], ensure_ascii=False)}`; '
        f'известных упоминаний: {pair["eligible_mentions"]}; неизвестных исключено: {pair["excluded_gold_unknown_mentions"]}.', '',
        f'Алерты session: TP={session.get("tp", 0)}, FP={session.get("fp", 0)}, FN={session.get("fn", 0)}, TN={session.get("tn", 0)}.', '',
        f'Алерты timely: TP={timely.get("tp", 0)}, FP={timely.get("fp", 0)}, FN={timely.get("fn", 0)}.', '',
    ]
    if report.get('detector_mode'):
        raw=report['raw_extraction_all_sessions']['micro']
        filtered=report['filtered_extraction_completed_sessions']['micro']
        lines.extend(['', '## Извлечение', '', '| Этап | Precision | Recall | F2 |', '|---|---:|---:|---:|',
            f'| GLiNER + regex, весь выбранный срез | {percent(raw["precision"])} | {percent(raw["recall"])} | {percent(raw["f2"])} |',
            f'| После фильтров, завершённые сессии | {percent(filtered["precision"])} | {percent(filtered["recall"])} | {percent(filtered["f2"])} |',
            '', 'Знаменатели различаются: второй срез исключает ошибки выполнения. Полные счётчики и разрезы по типам — в summary.json.', ''])
        lines.extend([f'Для оценки линкера сопоставлено {pair["matched_mentions"]}/{pair["gold_mentions_in_completed_sessions"]} эталонных упоминаний завершённых сессий.', ''])
    lines.extend(['## Расхождения', '', '| Сессия | Группы владельцев совпали | Эталонный первый алерт | Предсказанный |', '|---|---|---:|---:|'])
    mismatches = [row for row in report['cases'] if not row['owner_groups_exact'] or row['gold_first_alert'] != row['predicted_first_alert']]
    for row in mismatches:
        lines.append(f'| {row["session_id"]} | {"Да" if row["owner_groups_exact"] else "Нет"} | {row["gold_first_alert"]} | {row["predicted_first_alert"]} |')
    if not mismatches:
        lines.append('| Пока нет | — | — | — |')
    lines.extend(['', '## Неуспешные попытки', ''])
    for row in failed:
        lines.append(f'- `{row["session_id"]}`, попытка {row["attempt"]}: `{row.get("error_type", "unknown")}`, сообщение {row.get("message_index", "—")}. {row.get("error", "")} История сохраняется после успешного повтора.')
    if not failed:
        lines.append('Нет.')
    lines.extend(['', '## Файлы и воспроизведение', '',
        '[Метрики и случаи](results/summary.json), [состояние](results/manifest.json), [журналы](logs/).', '',
        'Команда обновления из корня worktree (без API):', '', '```powershell',
        f'.\\.venv-gliner\\Scripts\\python.exe -X utf8 tools/watch_gemma_linker.py --run {run.relative_to(ROOT).as_posix()} --once', '```', '',
        'Для постоянного обновления запустите ту же команду без `--once`. Наблюдатель не перезапускает оценщик и не делает запросы к модели.', ''])
    target = run / 'live-progress.md'
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(lines), encoding='utf-8')
    temporary.replace(target)
    return done == total or not active


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--run', type=Path, default=ROOT / 'data/experiments/gemma-linker-250-2026-11-24')
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(ROOT / 'data'):
        parser.error('Каталог запуска должен находиться в data/')
    while True:
        try:
            finished = render(run)
            if finished or args.once:
                return
        except (OSError, ValueError, KeyError) as exc:
            # Оценщик может находиться посередине записи JSON; повторяем чтение.
            if args.once:
                raise
            print(f'Повтор чтения после {type(exc).__name__}', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    main()
