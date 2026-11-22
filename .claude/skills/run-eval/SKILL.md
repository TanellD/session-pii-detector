---
name: run-eval
description: Запуск оценки модуля сессионной детекции ПДн на датасете диалогов. Использовать, когда нужно прогнать бенчмарк или воспроизвести числа из отчёта.
---

# Запуск оценки

Gold evaluation адаптера оценивает extraction; сессионные метрики не рассчитываются без согласованных `t_gold` и политики алерта.

## Подготовка
1. Окружение: `.\.venv-task12-locked\Scripts\python.exe`; для вывода кириллицы в PowerShell установить `$env:PYTHONUTF8='1'`.
2. Данные: `data/gold_set/session-alerts-v1/sessions.json`; split читается из соседнего `manifest.json`. Перед запуском зафиксировать версию и SHA-256 набора.
3. Модельный режим требует подготовленную локальную модель в `outputs/task12-prepared-model/` и зависимости из locked venv.

## Прогон

Сначала sweep threshold только на development:

```powershell
$env:PYTHONUTF8='1'
foreach ($t in 0.5, 0.6, 0.7) {
		.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold `
			--gold data/gold_set/session-alerts-v1/sessions.json `
			--split development --with-model --threshold $t `
			--output "runs/gold-eval/dev-model-t$t.json"
}
```

Затем выбрать порог по заранее заявленной метрике и один раз запустить test; не подбирать threshold по test. Для воспроизведения dev latency при threshold `0.5`:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.eval_gold `
	--gold data/gold_set/session-alerts-v1/sessions.json `
	--split development --with-model --threshold 0.5 `
	--measure-latency --latency-repeats 3 `
	--output runs/gold-eval/dev-model-t0.5-latency.json
```

## Что получится
JSON в `runs/gold-eval/` содержит exact и type-aware micro P/R/F2, счётчики и ошибки. `--measure-latency` добавляет p50/p95/max на сообщение после прогрева; загрузка модели исключена, замер не представляет полный сервис или конкурентную нагрузку. Формулы extraction — в `docs/kb/metrics.md`, методика полного latency — в `docs/kb/latency-budget.md`.

## Типичные проблемы
- `--threshold` действует только в `--with-model`; при отсутствии override используется config threshold.
- Model outputs могут содержать предупреждения Transformers о tokenizer/length; зафиксировать их при публикации результатов.
- Результаты нельзя воспроизвести по одному git commit, если рабочее дерево было dirty; сохранить правки или commit идентификатор.
- `F2-session`, `F2-timely`, offset и FP0 будут `N/A`, пока нет согласованных полей и политики алерта.
