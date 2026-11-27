# Эксперимент: оценка адаптера на gold-наборе

- Дата: 2026-11-21
- Автор: Сизова Олеся
- Issue: задачи адаптера и набора — [#32](https://github.com/TanellD/session-pii-detector/issues/32), [#36](https://github.com/TanellD/session-pii-detector/issues/36), прогон на наборе [#33](https://github.com/TanellD/session-pii-detector/issues/33)
- Гипотезы: добавленные regex/правила адаптера улучшают точность извлечения относительно базового regex, а локальная GLiNER-модель может дать дополнительные TP ценой ложных срабатываний; regex-цепочка адреса и дополнительные правила адаптера расширяют покрытие; threshold GLiNER, выбранный только на development, задаёт компромисс exact F2 и FP.

## Настройка

- Данные: `session-alerts-v1`, schema `annotation-mvp-v2`, taxonomy `mvp-name-parts-age-v1`.
- SHA-256 `sessions.json`: `c2bb3ac36f8546f3b41f663439aa66cec054bba7f6aab8d6e199643b64ad4631`.
- Размер выборки: 16 сессий: 8 development, 8 test; всего 41 mapped-эталонное упоминание после исключения внеобластьных типов.
- HEAD до локальных правок: `51bc169636f50b940a889ea27b90f291001627d0`; результаты получены на dirty working tree с правками `regex_detector.py`, adapter rules и `eval_gold.py`, поэтому одного HEAD недостаточно для точного воспроизведения.
- Детектор: `pii_smoke_rules-v2` для regex-only; для model-прогона локальная подготовленная модель из `outputs/task12-prepared-model/`.
- Конфиг модели: `benchmarks/pii_smoke/config.json`, SHA-256 `14481db2cba5b38eb8b495bb32200341be22bff0950d1b8b3def9e19a41e91b0`; model `urchade/gliner_multi-v2.1`, revision `443d26d654e0324125a96bebd8e796c14ff2efe6`, threshold `0.5`, threads `2`, repeats `3`.
- Изменения: regex-цепочка `ADDRESS` (индекс, город, улица, дом, необязательные корпус/квартира); regex-формы инициалов; `resolve_conflicts` сохраняет непересекающиеся упоминания и разрешает однотипное пересечение в пользу более длинного span. `eval_gold` читает split из manifest; `--threshold` передаётся в `predict_entities`.
- Железо: HUAWEI NBLK-WAX9X, AMD Ryzen 5 3500U with Radeon Vega Mobile Gfx (4 ядра / 8 логических потоков), 6.94 GiB RAM; Windows 10 Home Single Language 10.0.19045, build 19045. GLiNER запускалась на CPU, `threads=2`.

## Команды воспроизведения

```powershell
$env:PYTHONUTF8='1'
foreach ($t in 0.5, 0.6, 0.7) {
		.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold `
			--gold data/gold_set/session-alerts-v1/sessions.json `
			--split development --with-model --threshold $t `
			--output "runs/gold-eval/dev-model-t$t.json"
}
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split all --output runs/gold-eval/regex-all.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split development --output runs/gold-eval/regex-development.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split test --output runs/gold-eval/regex-test.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split development --with-model --threshold 0.5 --measure-latency --latency-repeats 3 --output runs/gold-eval/dev-model-t0.5-latency.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split test --with-model --threshold 0.5 --output runs/gold-eval/test-model-t0.5.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split all --with-model --threshold 0.5 --output runs/gold-eval/all-model-t0.5.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold --gold data/gold_set/session-alerts-v1/sessions.json --split development --measure-latency --latency-repeats 2 --output runs/gold-eval/latency-regex-dev.json
```

## Результаты извлечения

Метрики считаются по протоколу извлечения `docs/kb/metrics.md`: спан с границами и типом, micro-агрегация, взаимно однозначный матчинг. Precision и Recall вычисляются из TP/FP/FN; F2 по формуле из протокола.

| Прогон | Разрез | Порог | Exact TP/FP/FN | P | R | F2 | Type-aware TP/FP/FN | F2 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| regex-only | development | — | 8/0/11 | 1.000 | 0.421 | 0.476 | 8/0/11 | 0.476 |
| regex-only | test | — | 8/0/14 | 1.000 | 0.364 | 0.417 | 8/0/14 | 0.417 |
| regex-only | all | — | 16/0/25 | 1.000 | 0.390 | 0.444 | 16/0/25 | 0.444 |
| GLiNER + rules | development | 0.5 | 11/3/8 | 0.786 | 0.579 | **0.611** | 11/3/8 | 0.611 |
| GLiNER + rules | development | 0.6 | 10/3/9 | 0.769 | 0.526 | 0.562 | 10/3/9 | 0.562 |
| GLiNER + rules | development | 0.7 | 10/2/9 | 0.833 | 0.526 | 0.568 | 10/2/9 | 0.568 |
| GLiNER + rules | test | 0.5 | 15/7/7 | 0.682 | 0.682 | 0.682 | 17/5/5 | **0.773** |
| GLiNER + rules | all | 0.5 | 26/10/15 | 0.722 | 0.634 | 0.650 | 28/8/13 | 0.700 |

Порог 0.5 выбран по максимальному exact F2 на development. Test не использовался при выборе порога. Type-aware разрешает совпадение span при несовпадении типа; поэтому его счётчики отличаются от exact.

### Latency

| Режим | Измерения | p50 | p95 | max |
|---|---:|---:|---:|---:|
| regex-only, dev | 44 | 0.081 ms | 0.168 ms | 0.237 ms |
| GLiNER + rules, dev, threshold 0.5 | 66 | 772.763 ms | 958.426 ms | 1494.451 ms |

Замер — последовательное время извлечения одного сообщения после одного прогрева; загрузка модели исключена. GLiNER прогон: 22 сообщения × 3 повтора, p95 — nearest-rank. Это latency адаптера, не полный API/service latency: нет конкурентной нагрузки, очереди, сетевого времени, storage и сессионной линковки; согласованный latency budget отсутствует.

Отчёты: `runs/gold-eval/regex-all.json`, `regex-development.json`, `regex-test.json`, `dev-model-t0.5.json`, `dev-model-t0.6.json`, `dev-model-t0.7.json`, `dev-model-t0.5-latency.json`, `test-model-t0.5.json`, `all-model-t0.5.json`, `latency-regex-dev.json`.

## Сессионные метрики и исключения

- `F2-session`, `F2-timely`, offset и FP0: `N/A` — в `sessions.json` нет согласованных полей `t_gold`/`t_pred`, статусов положительной/отрицательной сессии и принятой политики алерта. Значения не рассчитаны и не интерпретируются как ноль.
- Unknown-сессии для извлечения: не применимо; для сессионных метрик — `unknown=неразмечено`, не исключалось искусственно.
- Out-of-scope/ unmapped типы `DATE_OF_BIRTH`, `RELATIONSHIP`, `SPECIAL_CATEGORY`, `PERSON`, `FULL_NAME` исключены из числителя и знаменателя extraction-прогона. Остальные mapped-типы берутся из `ADAPTER_TO_GOLD`.
- Небольшая выборка; результаты нельзя считать production-quality baseline.

## Анализ ошибок

1. Regex-only остаётся precision-oriented: на all он дал 7 TP по `email` и 7 TP по `phone`, без FP, но не дал TP по `address`, частям ФИО и большинству остальных классов.
2. Regex-цепочка адреса реализована, но качество не подтверждено: в gold all остаются 3 address FN без TP; на dev — 2 FN. В актуальном model smoke `addr_full_chain` и `addr_street_first` всё ещё возвращают `CITY` вместо ожидаемого `ADDRESS`, а `addr_city_street_number` возвращает лишний `CITY` вместе с `ADDRESS`.
3. На dev лучший exact F2 у threshold 0.5. При 0.7 precision выше (0.833 против 0.786), но TP меньше и exact F2 ниже (0.568 против 0.611). На test при зафиксированном 0.5 exact и type-aware F2 равны 0.682 и 0.773.
4. В model all exact остаются 10 FP (email 5, phone 2, geography 3) и 15 FN; основные пропуски включают address 3, first_name 4, individual_inn 1, bank_card 1 и occupation 2. Type-aware даёт 28 TP за счёт совпадений границ при несовпавшем типе.
5. Средняя величина latency доминируется GLiNER: p50 около 773 ms на сообщение против 0.081 ms regex-only. Это измерение только адаптера на одном локальном CPU и не задаёт вывод о полном сервисе.

## Вывод и следующий шаг

Regex-only является precision-oriented baseline на текущем наборе: exact P=1.000, exact R=0.390, F2=0.444 на all. Порог GLiNER 0.5 выбран на dev; он повышает exact F2 all до 0.650, но создаёт FP. Адресное правило добавлено, однако на gold-наборе TP пока не подтверждены.

Следующий шаг — проверить граничные формы адресной цепочки по synthetic cases и исправить расхождения smoke; затем расширить независимую выборку. Для сессионных метрик по-прежнему нужны согласованная схема алерта и `t_gold`-основание для F2-session/F2-timely/offset/FP0.
