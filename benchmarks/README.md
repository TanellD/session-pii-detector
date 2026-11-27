# benchmarks/

Скрипты оценки качества и латенси строго по `docs/kb/metrics.md` (после того как метрики определены). Результаты прогонов — в `runs/` (не в git), сводка — в `docs/experiments/`.

Адаптер реализуется отдельно в `benchmarks/adapter/`. Пакеты `pii_smoke` сохраняют backward-compatible wrapper-модули для существующих команд и тестов.

Команды smoke-проверки адаптера:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.adapter.adapter_check --output runs/adapter-check/regex-only.json
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.adapter.adapter_check --with-model --output runs/adapter-check/with-model.json
```

## Gold evaluation

`eval_gold` сравнивает extraction с `session-alerts-v1`; `--split` принимает
`development`, `test` или `all`. Подбор порога выполняется только на development.
Явный `--threshold` передаётся GLiNER; без него используется значение из
`benchmarks/pii_smoke/config.json`.

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold `
	--gold data/gold_set/session-alerts-v1/sessions.json `
	--split development --with-model --threshold 0.5 `
	--output runs/gold-eval/dev-model-t0.5.json
```

Опциональный `--measure-latency --latency-repeats 3` прогревает обработчик на
одном сообщении, затем замеряет per-message extraction; статистика p50/p95/max
добавляется в JSON. Время загрузки модели исключено. Это latency адаптера, не
полный API/service latency и не concurrency-тест. Формулы extraction описаны в
`docs/kb/metrics.md`, границы полного замера — в `docs/kb/latency-budget.md`.
