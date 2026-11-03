# Замечания аудита #12 исправлены и проверены локально

- Дата: 02.11.2026.
- Автор: `Kopylov-Andrey`.
- Связано: [исходный аудит](archive/2026-11-02-research-audit.md), #12.
- Код исправлений: `d5ab66b10b7fbc9ce921aa79b19fd2b41c9ef63f`.

## Что изменено и чем проверено

| Замечание | Исправление | Подтверждение |
|---|---|---|
| Установка зависела от системы и pip internals | Полный Windows/Python lock, CPU torch и публичный truststore; venv без наследования | Установка lock во второе пустое окружение; pip check, тесты и inference прошли |
| Backbone скачивался по main | Обе модели по SHA; локальный backbone, manifest/hashes, strict=True | Подготовка и inference с пустым HF_HOME и offline прошли |
| report падал для regex-only | Модельные поля и режимы обрабатываются по наличию | Полный CLI regex-only → report в runs/task12-regex-v2 прошёл; старый audit-regex также читается |
| report повторял старые выводы | Проверенные числа и фактические расхождения; ручной анализ отдельно | Безошибочные predictions не дают прежнего негативного вывода; порча качества/латенси отклоняется |
| API не позволял восстановиться | Явный create, GET snapshot, reset с preconditions; state в ошибках; analyze не создаёт epoch | Schema, продолжение, retry, конфликт и reset проверены |
| Противоречивые ответы проходили проверку | Инварианты покрытия, исторического алерта, person_id и has_signals; ValueError вместо assert | Все четыре воспроизведения аудита отклоняются; регрессия сохранена |
| Полное ФИО и отчество отсутствовали | Версия v2, таблица классов/меток/случаев | ФИО из трёх компонентов и отчество найдены как PERSON; адаптер частей не заявляется реализованным |

Команды окружения — [README](../../benchmarks/pii_smoke/README.md).
Проверки после установки lock:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m pip check
.\.venv-task12-locked\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
.\.venv-task12-locked\Scripts\python.exe tools/validate_api_examples.py
.\.venv-task12-locked\Scripts\python.exe -m ruff check benchmarks/pii_smoke tools/validate_api_examples.py tests
.\.venv-task12-locked\Scripts\python.exe -m ruff format --check benchmarks/pii_smoke tools/validate_api_examples.py tests
$env:PRE_COMMIT_HOME = Join-Path (Get-Location) '.cache-research/pre-commit'
.\.venv-task12-locked\Scripts\python.exe -m pre_commit run --all-files
git diff --check
```

Результат: 12 unittest-тестов и 23 группы положительных/отрицательных API-проверок.
Это проверки документов и инструментов, не runtime-тесты HTTP. Песочница Windows
блокировала временные файлы unittest; с обычными разрешениями на Temp тесты прошли.
Установка/подготовка модели требовали сетевых прав оболочки. TLS-проверка не
отключалась, глобальные пакеты не менялись.

## Независимый повтор исходного результата

`runs/task12-v1-clean-replay/`: исходный v1, новый lock-env, локальный pinned
backbone, strict=True, пустой HF_HOME, offline. В каждом из трёх режимов predictions
и scores **точно совпали** с `runs/task12-smoke/`. Dataset/config hashes совпадают.
Оба полных новых run зафиксировали clean worktree и коммит d5ab66b.

Получение агрегатов и сверка:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-v1-clean-replay
@'
import json
from pathlib import Path
for mode in ('regex', 'gliner', 'combined'):
    old = json.loads(Path(f'runs/task12-smoke/{mode}.json').read_text(encoding='utf-8'))
    new = json.loads(Path(f'runs/task12-v1-clean-replay/{mode}.json').read_text(encoding='utf-8'))
    equal = all(a['id'] == b['id'] and a['predicted'] == b['predicted'] for a, b in zip(old, new, strict=True))
    print(mode, equal)
'@ | .\.venv-task12-locked\Scripts\python.exe -
```

Новый [эксперимент v2](../experiments/2026-11-02-gliner-cpu.md) добавляет
случаи имён. По нему обновлены слайды и [покрытие классов](../research/notes/pii-class-coverage.md).
Исходные data, run, отчёт v1 и audit-replay сохранены отдельно.

## Что это значит для проекта

Подтверждённые недоделки локальной исследовательской работы устранены. Материалы
готовы к ревью #12; production-готовность детектора не доказана. Сохраняются
измеренные ошибки PERSON/должности/родства, межтиповые конфликты и ограничения
переноса на реальные тексты. Предупреждения токенизатора не объявлены причиной ошибок.
Другие модели не запускались: это следующий этап, а не скрытый результат сравнения.

Классы/пороги/роли/TTL и совместимость с NER HiveTrace требуют решения команды.
Сервис, линковка и сессионный F2 относятся к MVP. Push, PR, комментарии, правки
таблиц и публикация не выполнялись.
