# Пробный smoke-прогон адаптера

Дата: 2026-11-20. Статус: подготовлен, фактический with-model run зависит от локального окружения.

## Цель

Проверить, что адаптер преобразует regex/GLiNER-сущности в единый Mention-контракт:
метку, исходные границы, confidence, confidence_kind, source и detector_version.
Дополнительно проверяется стабильность ID и разборы FULL_NAME/частей ФИО.

## Вход

Синтетический набор `benchmarks/pii_smoke/data/synthetic-attributes.json`.
Набор не содержит `t_gold`, не является blind test и не является независимой оценкой.
Формат описан в `docs/data/synthetic-attributes.md`.

Кейсы размечены полем `requires`:

- `requires: "regex"` — проверяется и без модели, и с моделью;
- `requires: "model"` — без `--with-model` пропускается (`skipped`), чтобы не давать ложный failure; с `--with-model` проверяется.

## Выход

JSON со списком cases и Mention-полями для линковщика. В проверке:

- regex confidence должен быть `null` с kind `none`;
- GLiNER confidence должен быть model_score, но не probability правильности;
- исходные границы должны ссылаться на исходную строку;
- одинаковый текст/type/source/version должен получать одинаковый ID;
- межтиповые конфликты не должны тянуться в сессионный алерт;
- кейсы с `requires: "model"` без модели помечаются `skipped`, а не `failure`.

## Что не измеряется

- Сессионный F2 и timely offset.
- Качество GLiNER/production-модели.
- Полная принадлежность атрибутам человеку.
- Нормализация дат, телефонов и адресов.
- Независимое обобщение по реальным обезличенным сессиям.

## Команды

Без модели (regex-only; кейсы `requires: model` пропускаются):

```powershell
$env:PYTHONUTF8='1'
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.adapter_check `
  --output runs/adapter-check/regex-only.json
```

С моделью (после `prepare.py`; проверяются все кейсы):

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.adapter_check `
  --with-model `
  --output runs/adapter-check/with-model.json
```

Без `--output` результат печатается в stdout. При расхождениях — exit code 1 и
список `failures` в JSON.

Для unit-проверок:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
```

Любой реальный run должен фиксировать commit, SHA config и detector revision.
Числа отчёта должны воспроизводиться этими командами; смоук не публикуется как
результат качества модели.

## Ожидаемые результаты

- `regex-only`: `email`, `inn_phone_conflict`, `organization` проходят;
  `age`, `birth_date`, `full_name`, `surname`, `patronymic` — `skipped`;
- `with-model`: все кейсы проверяются; `full_name` даёт три типа
  (`FIRST_NAME`, `PATRONYMIC`, `LAST_NAME`), `inn_phone_conflict` — только `INN`
  (PHONE подавлен по overlap);
- при расхождении — exit code 1 и список `failures`.

`adapter_check` сравнивает набор типов, не порядок. Порядок упоминаний проверяется
отдельным unit-тестом (`test_adapter_full_name_rules_and_inn_priority`) и
контрактом: упоминания идут по `start`, не семантически.

## Границы

`inn_phone_conflict` без модели не воспроизводит конфликт: regex даёт только `INN`,
`PHONE` не появляется. С `--with-model` GLiNER может дать `PHONE` на тех же цифрах —
тогда suppression по overlap сработает. Это реальная проверка конфликта.

## Фактический статус прогона

- `regex-only` запущен 20.11.2026: `ok=true`, `failures=[]`, 3 кейса проверены,
  5 `skipped` (`requires model`). Отчёт — `runs/adapter-check/regex-only.json`.
- `with-model` запущен 20.11.2026 после `prepare`: `ok=true`, `failures=[]`,
  все 8 кейсов проверены. Отчёт — `runs/adapter-check/with-model.json`.
- `organization` до правки давал `PERSON` на местоимении «Я»; после подавления
  `PERSON` на местоимениях (`PRONOUNS` в `adapter_rules.py`) — только `ORGANIZATION`.
- `inn_phone_conflict` с моделью конфликт не воспроизведён: GLiNER не дал `PHONE`
  на 12 цифрах, suppression не проверен. Это ограничение baseline, не баг адаптера.
