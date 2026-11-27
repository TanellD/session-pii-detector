# Пакет данных для поиска дешёвого линкера

- Дата: 2026-11-26.
- База: 3733082 плюс рабочие изменения; пакет данных — cheap-linker-benchmark-v1.
- Инструкция: [контракт, запуск, live-отчёт и подбор порогов](../data/cheap-linker-benchmark.md).
- Исходные данные: session-benchmark-2400-v5, замороженный regex + GLiNER v2.

## Что подготовлено

Каталог и ZIP: `data/experiments/cheap-linker-benchmark-v1-2026-11-26`.
Состав: train 1440, validation 480, test 480 сессий. Входы отделены от targets;
сохранены исходные разбиения, эталон владельцев и независимые от весов сервиса
ожидаемые алерты. Кандидаты реального NER содержат provenance и полноту обработки.
В пакет включён локальный runtime без весов моделей и ключей API.

Команда воспроизведения состава и хешей (требует нового output):

```powershell
.venv-gliner/Scripts/python.exe -X utf8 tools/prepare_linker_dataset.py --dataset data/experiments/session-benchmark-2400-v5-2026-11-26 --ner data/experiments/benchmark-ner-only-2026-11-26 --output data/experiments/cheap-linker-benchmark-v1-2026-11-26
```

`manifest.json` фиксирует хеши payload. В архиве runtime-manifest.json фиксирует
включённые файлы кода/конфига. `--refresh-archive` проверяет существующий payload
и пересобирает ZIP с текущим runtime, не меняя датасет.

## Проверка

Тривиальный контрольный метод one_person связывает все атрибуты с одним человеком.
Он проверяет работоспособность интерфейса, не предлагается как решение задачи.

```powershell
.venv-gliner/Scripts/python.exe -X utf8 tools/run_linker_benchmark.py --package data/experiments/cheap-linker-benchmark-v1-2026-11-26 --split validation --mode gold --linker cheap_linker_baselines:one_person --run data/experiments/linker-package-smoke-gold-2026-11-26
.venv-gliner/Scripts/python.exe -X utf8 tools/run_linker_benchmark.py --package data/experiments/cheap-linker-benchmark-v1-2026-11-26 --split validation --mode real-ner --linker cheap_linker_baselines:one_person --run data/experiments/linker-package-smoke-real-ner-2026-11-26
```

Gold: 480/480 сессий без ошибок. Real-ner: 466/480, 14 ошибок
Unresolved cross-type overlap до вызова линкера. Данные не фильтровались для
успешного прохождения. Повтор завершённого прогона требует --resume и неизменного
кода/настроек; после изменения кода надо использовать новый run.

```powershell
.venv-gliner/Scripts/python.exe -X utf8 tools/calibrate_cached_risk.py --run data/experiments/linker-package-smoke-real-ner-2026-11-26 --thresholds-only --allow-incomplete
.venv-gliner/Scripts/python.exe -X utf8 tools/watch_cached_validation.py --run data/experiments/linker-package-smoke-real-ner-2026-11-26 --once
.venv-gliner/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_linker_benchmark.py
```

Получены отчёт baseline, live-документ и калибровка на успешном подмножестве.
Проверены отсутствие эталонных owners в кандидатах, последовательное построение
состояния из собственных ответов и восстановление записи при временной блокировке
файла Windows. Время прямого predict измеряется отдельно от записи кэша.

Дополнительно архив распакован в outputs/linker-package-portability-check.
В его корне повторены команды runner (real-ner, validation), calibrate и watcher
из инструкции с run=data/experiments/portable-smoke-validation. Результат также
466 успешных и 14 пересечений. Это проверка переносимости кода/данных; веса
GLiNER и внешний API не использовались, test не оценивался.

## Ограничения

Пакет наследует авторские шаблоны, выборочное ревью и неоднозначную типизацию
некоторых частей имён. Нет новой независимой разметки. Статические supervised
примеры используют прошлое эталонное состояние, runtime — собственные ответы.
F2 сервиса нельзя использовать как единственную метрику линкера: тривиальное
объединение людей может сохранять алерты при неправильной атрибуции.
Сопоставлять также pairwise, покрытие упоминаний, ошибки выполнения и время.

Калибровка не запускает модели. Изменение кандидатов, нормализации или контекста
может требовать нового прохода линкера; изменение только весов и порогов риска — нет.
Пакет и результаты остаются вне git, инструменты и документация — в репозитории.
