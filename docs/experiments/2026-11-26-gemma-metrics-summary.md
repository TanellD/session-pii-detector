# Метрики Gemma-линера

Gemma-4-31B, реальные кандидаты regex + GLiNER v2, validation набора
`session-benchmark-2400-v5`. Завершено 448/480 сессий; исключены 19 ошибок
ответа линкера и 13 пересечений типов до линкера.

| Метрика линкера | Значение |
|---|---:|
| Pairwise precision | 98.04% |
| Pairwise recall | 97.56% |
| Pairwise F1 | 97.80% |

Оценка только на 620 точно совпавших по границам и типу упоминаниях из 1368
эталонных в успешных сессиях. Неизвестные эталонные владельцы исключены из
pairwise. Эти показатели не учитывают пропуски NER и не означают такое же
качество всего сервиса.

После подбора порогов риска на том же validation, при неизменных весах:

| Алерты сервиса | Precision | Recall | F2 |
|---|---:|---:|---:|
| Session | 64.23% | 97.24% | 88.18% |
| Timely | 61.51% | 92.09% | 83.76% |

Пороги: известный владелец 0.35, неизвестная принадлежность 0.60.
Это предварительные результаты подбора на успешном подмножестве validation,
не независимая оценка test. Runtime-конфиг автоматически не изменён.

Источники вне git:
`data/experiments/benchmark-validation-gemma-risk-2026-11-26/online-summary.json`
и `calibration/report.json` в том же каталоге.
Воспроизвести числа из сохранённых отчётов из корня worktree:

```powershell
python -X utf8 -c "import json; from pathlib import Path; p=Path('data/experiments/benchmark-validation-gemma-risk-2026-11-26'); print(json.loads((p/'online-summary.json').read_text(encoding='utf-8'))['partial_metrics']['linker']); r=json.loads((p/'calibration/report.json').read_text(encoding='utf-8')); print(r['results']['thresholds_only']['validation']); print(r['results']['thresholds_only']['policy'])"
```

Подробнее: [подбор порогов](2026-11-26-validation-thresholds-only.md).
