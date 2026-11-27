# Живой процесс разметки не означает работающую сеть

- Дата: 2026-11-24.
- Автор: команда проекта.
- Связано: [сравнение корпусов](../experiments/2026-11-23-andrey-500-comparison.md).

## Что увидели

Фоновые процессы и наблюдатель оставались живыми, но в последних батчах обоих
разметчиков повторялись `URLError` без новых успешных ответов. Причина этих
ошибок по прежнему журналу не установлена: он сохранял только класс исключения.
Новые короткие сетевые пробы к Gemma и Qwen прошли успешно. Нельзя считать
само наличие процесса доказательством прогресса или объявлять сбойные сессии
отрицательными.

## Доказательство

Прежние батчи зафиксированы после остановки проверенных процессов. Для каждого
изменённого манифеста сохранена побайтовая копия `*-before-interruption-*.json`.
Запросы, ответы и завершённые аннотации не менялись. Незавершённые записи
помечены `InterruptedForRecovery`; это не ошибка качества извлечения.

Подготовка новых очередей воспроизводит 111 уже завершённых сессий и 889
оставшихся для первого набора, 25 завершённых и 475 оставшихся для второго:

```powershell
python -X utf8 tools/prepare_annotation_recovery.py --source data/experiments/annotation-1000-2026-11-23 --previous data/experiments/radeon/annotation-1000-gemma-2026-11-23-v2 --previous data/experiments/radeon/annotation-1000-gemma-2026-11-23-recovery/batches --output data/tmp/annotation-restart-first-new
python -X utf8 tools/prepare_annotation_recovery.py --source data/experiments/andrey-scenarios-500-2026-11-23 --previous data/experiments/radeon/andrey-500-qwen-2026-11-23 --previous data/experiments/corpus-comparison-1000-500-2026-11-23/recovery-second-01/batches --output data/tmp/annotation-restart-second-new
```

Команды выполняются из `outputs/session-adapter-integration`; выходные каталоги
должны быть новыми. Фактическое продолжение находится в
`data/experiments/annotation-resume-2026-11-24/{first,second}/batches/`.

После перезапуска первый запрос Gemma в этой очереди получил валидный ответ;
источник доказательства — её `batch-001/run/manifest.json`, поле `steps`.
Сам по себе этот шаг не означает завершения очереди или подтверждения качества
всей разметки.

## Что это значит для проекта

При продолжении нужно проверять и процесс, и последние результаты. Наблюдатель
теперь восстанавливает список прежних прогонов и счётчик повторов из
`watch-state.json`, добавляя новую очередь без потери прежнего покрытия и
коллизий имён дальнейших восстановлений. У новых сетевых ошибок сохраняются
класс причины и системные коды без ключей, заголовков и текста запроса.

Проверки сохранения ответов и восстановления состояния:

```powershell
python -X utf8 -m unittest discover -s tests -p test_annotation_recovery.py
python -X utf8 -m unittest discover -s tests -p test_sequential_annotation.py
```
