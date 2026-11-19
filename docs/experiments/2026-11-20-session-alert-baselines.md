# Первый алерт на синтетических сессиях

- Дата: 20.11.2026.
- Issue: [#41](https://github.com/TanellD/session-pii-detector/issues/41).

Предполагали, что проверка одной реплики пропускает алерты, требующие
накопления, а объединение атрибутов разных людей даёт ложные алерты.

## Настройка

- [Набор `session-alerts-v1`](../data/session-alerts-pilot.md). SHA-256 корпуса
  `c2bb3ac36f8546f3b41f663439aa66cec054bba7f6aab8d6e199643b64ad4631`,
  эталона `156c80da4690bc69be69d226a0f565f7f50c22c55024692a427f1eb469781e1d`.
- Код прогона `7fdbd8f798d306bb08de1fecc51fb6cea1cf212a`.
  ТЗ PR #44 на коммите `022bf6290c8107149200efcc436c782da7c2995f`.
- Политика `2026-11-18-working-v2`, режим калькулятора `policy_only`.
  SHA-256 политики `ece26b8e87e0fb7dd979744bda30d758c518755120b4a6feafc675a05de8a0a1`.
- Конфиг `benchmarks/pii_smoke/config.json`, SHA-256
  `bb84bac2b8074b3d487eb20c3f2dfb4c3efe415b92aad940a22bc513654dc1e8`.
  GLiNER multi-v2.1 с backbone из [smoke-прогона](../../benchmarks/pii_smoke/README.md).
  CPU, Windows, Python 3.13.0, GLiNER 0.2.24, Torch 2.11.0+cpu.

При одинаковых упоминаниях и политике сравнили проверку одного сообщения,
накопление со слиянием известных владельцев и накопление по людям.
Атрибуты с `person_id=null` сохраняют неизвестную принадлежность.

Сравнение сделали на двух входах. Первый — эталонные спаны и владельцы.
Второй — regex + GLiNER, один вызов на сообщение. Локальный адаптер
преобразует метки, привязывает только явные высказывания о себе и
принимает полные документы без проверки контрольной суммы.

```powershell
python -X utf8 tools/prepare_session_corpus.py data/session-alerts-v1 --with-alerts
python -X utf8 tools/run_session_baselines.py --corpus data/session-alerts-v1 --output runs/session-alerts-replay --model-dir outputs/task12-prepared-model
python -X utf8 tools/evaluate_session_alerts.py --gold data/session-alerts-v1/gold.json --predictions runs/session-alerts-replay/predictions.json --output runs/session-alerts-replay/recalculated.json
```

В `--model-dir` укажите каталог подготовленной модели. Вместо него можно
передать `--gold-only` для эталонного режима или `--regex-only` для
форматных правил. Для `--output` выберите новый каталог. Сохранённый
прогон лежит в `runs/session-alerts-v1-reviewed/`. Повторный расчёт
совпал с исходным `metrics.json`.

## Результаты проверочной части

В расчёт вошли все 8 сессий test (3 положительные, 5 отрицательных).
Пометки неуверенности есть в 6 сессиях. В 2 из них позже сработало
правило. Ранних и поздних алертов не было, поэтому F2-timely и
F2-session совпали. Для всех TP offset равен 0.

| Вход и стратегия | TP / FP / FN / TN | F2-timely | F2-session | FP0 все отриц. | FP0 уверен. отриц. |
|---|---|---:|---:|---:|---:|
| Эталон, одно сообщение | 2 / 0 / 1 / 5 | 0,714 | 0,714 | 0/5 | 0/1 |
| Эталон, объединённые люди | 3 / 1 / 0 / 4 | 0,938 | 0,938 | 1/5 | 0/1 |
| Эталон, по людям | 3 / 0 / 0 / 5 | 1,000 | 1,000 | 0/5 | 0/1 |
| Regex + GLiNER, одно сообщение | 2 / 0 / 1 / 5 | 0,714 | 0,714 | 0/5 | 0/1 |
| Regex + GLiNER, объединённые люди | 2 / 0 / 1 / 5 | 0,714 | 0,714 | 0/5 | 0/1 |
| Regex + GLiNER, по людям | 2 / 0 / 1 / 5 | 0,714 | 0,714 | 0/5 | 0/1 |

`metrics.json` содержит счётчики по обеим частям, offset, причины
неуверенности и исключения. На test адаптер получил 13 точных совпадений
по спану и типу, 1 лишний спан и 9 пропусков. Спаны и причины отбрасывания
сохранены в `ner-diagnostics.json`.

## Анализ ошибок

- `test-01`. На эталонных спанах накопление связало имя и отчество с
  телефоном на третьем сообщении. GLiNER выдал `PERSON`, который адаптер
  не разделил на части имени. Все три стратегии с NER пропустили алерт.
- `test-02`. Слияние владельцев объединило ФИ покупателя с адресом
  получателя и дало ложный алерт на втором сообщении.
- `dev-02`. Слияние владельцев объединило телефон посетителя с почтой
  спутника. Ложный алерт входит и в срез уверенно отрицательных.
- `dev-06`. С regex + GLiNER накопление телефона и почты дало алерт
  на третьем сообщении. Проверка одной реплики его пропустила.

## Ограничения

Эталон ждёт независимой проверки. Итоговый прогон требует
[#34](https://github.com/TanellD/session-pii-detector/issues/34),
[#36](https://github.com/TanellD/session-pii-detector/issues/36) и
[#42](https://github.com/TanellD/session-pii-detector/issues/42).
Численные правила, адресное обогащение и задержку сервиса на пилоте
не проверяли. [Почему в отчёте два значения FP0](../insights/2026-11-20-fp0-negative-denominators.md).

## Тесты оценщика

```powershell
python -X utf8 -m unittest discover -s tests -p test_session_alert_evaluation.py
```
