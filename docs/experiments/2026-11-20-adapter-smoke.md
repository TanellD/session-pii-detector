# Пробный smoke-прогон адаптера

Подготовлено: 2026-11-20. Актуализировано: 2026-11-21.
Это диагностическая проверка преобразований, не независимая оценка качества.

## Цель

Проверить, что адаптер преобразует regex/GLiNER-сущности в единый Mention-контракт:
метку, исходные границы, confidence, confidence_kind, source и detector_version.
Дополнительно проверяется стабильность ID и разборы FULL_NAME/частей ФИО.

## Вход

Синтетический набор `benchmarks/adapter/data/synthetic-attributes.json`.
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
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.adapter.adapter_check `
  --output runs/adapter-check/regex-only.json
```

С моделью (после `prepare.py`; проверяются все кейсы):

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.adapter.adapter_check `
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

## Статус прогонов
Первый `regex-only` run от 20.11.2026: `ok=true`, `failures=[]`, 3 кейса проверены,
5 пропущены (`requires: model`) из тогдашних 8 кейсов.

 - Первый `with-model` run от 20.11.2026 после `prepare`: `ok=true`, `failures=[]`,
   проверены все 8 тогдашних кейсов. Отчёт — `runs/adapter-check/with-model.json`.
 - Текущий локальный отчёт `runs/adapter-check/with-model.json` содержит 41 кейс и
   `ok=false` с 6 расхождениями. Они соответствуют известным ограничениям из
   [покрытия адаптера](../research/notes/adapter-coverage.md); подробности и команда
   воспроизведения приведены выше. Этот результат не следует описывать как успешный
   полный прогон.
- `organization` до правки давал `PERSON` на местоимении «Я»; после подавления
  `PERSON` на местоимениях (`PRONOUNS` в `adapter_rules.py`) — только `ORGANIZATION`.
- `inn_phone_conflict` с моделью конфликт не воспроизведён: GLiNER не дал `PHONE`
  на 12 цифрах, suppression не проверен. Это ограничение baseline, не баг адаптера.
- Адаптер v2 дополнительно покрывает: разбор ФИО через `russiannames` и форматы
  с инициалами/двойной фамилией, подавление местоимений, вычисление `attributes.age`
  по ДР формата `ДД.MM.ГГГГ`, склейку ADDRESS/CITY и организационные маркеры
  `ООО/АО/ИП/ПАО/АНO`, а также reclassification личного/служебного email.
  - `SERVICE_DOMAINS` реализован как явный список доменов наряду с правилами
    `SERVICE_LOCAL_PARTS`; оба варианта проверяются синтетической фикстурой. Это
    подтверждает только работу правила на заданных примерах, не полноту классификации.
