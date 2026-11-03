# Исследование PII-детекторов: результат для ревью

Обновлено: 03.11.2026. Исполнитель: `Kopylov-Andrey`. Задача: #12.
Публикация, push, создание PR, правки issues и Google-таблиц не выполнялись.
Основная ветка upstream не изменялась. Результат готовится в `codex/12-detectors-api-research`.

**Статус:** локальные исследовательские материалы готовы к ревью. Подтверждённые
замечания аудита исправлены, установка и измерения повторены в чистом окружении.
[Что исправлено и чем проверено](../insights/2026-11-02-reproducibility-checks.md).

## Основания и границы задачи

Постановка: [issue #12](https://github.com/TanellD/session-pii-detector/issues/12).
База репозитория: `e1e05a2d1ba0618ab123abe25db416314df4e10c`.
Предложения [PR #16](https://github.com/TanellD/session-pii-detector/pull/16),
снимок `68b090e5db4664e3802e09a17976f45e1d7601bb`, рассматриваются как черновик.
Результат задачи: обзор аналогов, сравнение детекторов, CPU-проверка,
draft API и proposed ADR. Все эти материалы перечислены ниже.

## Что читать

1. [Сравнение аналогов и детекторов](../research/notes/pii-detectors-comparison.md),
   [реестр первичных источников](../research/sources.md).
2. [Критический разбор найденной статьи](../research/notes/dialogue-reidentification-paper.md).
3. [Актуальный CPU-эксперимент v2](../experiments/2026-11-02-gliner-cpu.md),
   [таблица покрытия классов](../research/notes/pii-class-coverage.md),
   [команды воспроизведения](../../benchmarks/pii_smoke/README.md),
   [инсайт](../insights/2026-11-02-ner-rule-conflicts.md).
4. [API v0.1](../api/session-api.md),
   [JSON Schema](../api/session-api.schema.json), [примеры](../api/session-api.examples.json).
5. [ADR-0003, proposed](../decisions/0003-detector-independent-session-layer.md).
6. [Содержание слайдов](../../deliverables/01-review-2026-11-05/detector-research-slides.md).

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
.\.venv-task12-locked\Scripts\python.exe -m pip check
.\.venv-task12-locked\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
.\.venv-task12-locked\Scripts\python.exe tools/validate_api_examples.py
.\.venv-task12-locked\Scripts\python.exe -m ruff check benchmarks/pii_smoke tools/validate_api_examples.py tests
.\.venv-task12-locked\Scripts\python.exe -m ruff format --check benchmarks/pii_smoke tools/validate_api_examples.py tests
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-smoke-v2
$env:PRE_COMMIT_HOME = Join-Path (Get-Location) '.cache-research\pre-commit'
.\.venv-task12-locked\Scripts\python.exe -m pre_commit run --all-files
git diff --check
```

JSON-проверки покрывают форму, Unicode offsets, согласованность вердикта,
людей, сигналов, продолжения/retry и reset; они не доказывают
корректность будущей реализации порядка/TTL/транзакций. Тесты эксперимента
проверяют checksum, сопоставление, отсутствие двойного TP и дефекты отчётности.
Проверены 12 unittest и 23 группы API-примеров. Исходные predictions/scores v1
точно воспроизведены в новом окружении; новое покрытие ФИО измерено отдельно в v2.
Исходные данные, замеры и версии моделей сохранены. Старые отчёты и аудит
перенесены в `docs/experiments/archive/` и `docs/insights/archive/`;
[правила хранения артефактов](../insights/2026-11-03-research-artifacts.md).
