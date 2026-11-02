# Задача #12 — локальный результат для ревью

Дата: 02.11.2026. Исполнитель: `Kopylov-Andrey`.
Публикация, push, создание PR, правки issues и Google-таблиц не выполнялись.
Основная ветка upstream не изменялась. Результат готовится в `codex/12-detectors-api-research`.

## Что читать

1. [Сравнение аналогов и детекторов](../research/notes/task-12-comparison.md),
   [реестр первичных источников](../research/sources.md).
2. [Критический разбор найденной статьи](../research/notes/task-12-paper.md).
3. [CPU-эксперимент и ошибки](../experiments/2026-11-02-task12-gliner-cpu.md),
   [команды воспроизведения](../../benchmarks/pii_smoke/README.md),
   [инсайт](../insights/2026-11-02-task12-detector-boundary.md).
4. [API v0.1](../api/session-pii-v0.1.md),
   [JSON Schema](../api/session-pii.schema.json), [примеры](../api/session-pii-examples.json).
5. [ADR-0003, proposed](../decisions/0003-detector-independent-session-layer.md).
6. [Содержание слайдов](../../deliverables/01-review-2026-11-05/task12-slides.md).

## Вывод

Baseline запускается на CPU, но готовность для MVP не доказана: нужны разрешение
конфликтов и работа с семантическими классами. Рекомендация — сохранить измеренный
baseline, проверить другие веса на независимом наборе и строить сессионный слой
независимо от детектора. По рассмотренным материалам нельзя заявлять отсутствие
аналогов или новизну общего принципа накопления по людям.

## Предел результата

Ни сервис, ни сессионный F2, ни качество линковки здесь не реализованы и не измерены:
они не входят в практическую проверку #12. API — проверенный на согласованность
документ, не работающий endpoint. Классы, пороги, warning, TTL, роли и исправления
ожидают решения команды; предложения PR #16 не выдаются за принятые требования.
Правовые утверждения не являются выводами нашего NER-эксперимента.

## Проверки

Из корня проекта, в установленном окружении:

```powershell
.\.venv-research\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -r tools/requirements-checks.txt
.\.venv-research\Scripts\python.exe -m unittest discover -s tests -p 'test_pii_smoke.py'
.\.venv-research\Scripts\python.exe tools/validate_task12.py
.\.venv-research\Scripts\python.exe -m ruff check benchmarks/pii_smoke tools/validate_task12.py tests/test_pii_smoke.py
.\.venv-research\Scripts\python.exe -m benchmarks.pii_smoke.report
$env:PRE_COMMIT_HOME = Join-Path (Get-Location) '.cache-research\pre-commit'
.\.venv-research\Scripts\python.exe -m pre_commit run --all-files
git diff --check
```

JSON-проверки покрывают форму примеров и Unicode offsets; они не доказывают
корректность будущей реализации порядка/TTL/транзакций. Тесты эксперимента
проверяют checksum, точность сопоставления и отсутствие двойного TP.
Итоговая сверка критериев и Git — в [плане](task-12-plan.md).
