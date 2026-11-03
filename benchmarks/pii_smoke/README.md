# Проверка regex + GLiNER для #12

Диагностика извлечения сущностей, не реализация сессионного сервиса и не замер
проектного F2. Метки, порог и gold фиксируются до запуска модели. Порог не
подбирается по этим примерам. Другие детекторы здесь не измеряются.

## Воспроизведение в PowerShell из корня репозитория

Проверяемая платформа: Windows x64, CPython 3.13.0. Полный снимок зависимостей,
включая CPU PyTorch, pip, TLS-библиотеку и транзитивные пакеты:
`requirements-win-py313.lock.txt`. Он рассчитан на эту платформу; для Linux/другого
Python нужен отдельный проверенный lock. Системные Python-пакеты не наследуются.

```powershell
py -3.13 -m venv .venv-task12-locked
.\.venv-task12-locked\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -r benchmarks/pii_smoke/requirements-win-py313.lock.txt
.\.venv-task12-locked\Scripts\python.exe -m pip check
$env:PYTHONUTF8 = '1'
$env:HF_HOME = Join-Path (Get-Location) '.cache-research\huggingface'
$env:HF_HUB_DISABLE_XET = '1'
Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
Remove-Item Env:TRANSFORMERS_OFFLINE -ErrorAction SilentlyContinue
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.prepare
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.dataset --version v2
.\.venv-task12-locked\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
.\.venv-task12-locked\Scripts\python.exe tools/validate_api_examples.py
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.run --output runs/task12-smoke-v2
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-smoke-v2
```

Повтору дать новый `--output`, например `runs/task12-smoke-v2-replay`: измеренные
summary не перезаписываются. Для regex достаточно `psutil` из requirements;
модель и HF-кеш не нужны. Полный lock удобен для общей проверки:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.run --regex-only --output runs/task12-regex-v2
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-regex-v2
```

Подготовка скачивает только публичные веса/конфиги. Model и backbone загружаются
по двум SHA из config, без lookup `main`. В игнорируемом
`outputs/task12-prepared-model/` создаются локальные веса, backbone и manifest
с SHA-256. Меняется только локальная копия gliner_config: путь backbone становится
локальным. HF-кеш не правится. После переноса папки нужно повторить prepare.
В Windows используется публичный `truststore` и доверенное хранилище ОС;
проверка TLS остаётся включённой. Тексты не передаются во внешние сервисы.

Run проверяет manifest/хеши и загружает модель с `strict=True`, только с CPU.
Он работает и с пустым HF_HOME при offline, поскольку все артефакты локальны.
В summary фиксируются время, CPU, версии, hashes, commit и dirty; `freeze.txt`
сохраняется автоматически. Сырые фразы/предсказания и модель не добавляются в git.
Report сверяет числа качества, латенси, повторы и состав набора с raw-файлами.
Ручная интерпретация конкретного run отделена от автоматического отчёта.

## Набор и ограничения

Генератор создаёт 30 синтетических фраз с gold spans в игнорируемом `data/`.
`v2` добавляет трёхкомпонентное ФИО и отдельное отчество вместо прежнего
двухкомпонентного имени и одного негативного числового случая. `--version v1`
точно восстанавливает исходный набор; его прежний SHA проверяется тестом.
Это расширение проверки покрытия после аудита, а не независимый test-set.
Порог и labels v1/v2 одинаковы. Результаты версий нельзя считать улучшением модели.

Имена и организации вымышлены, email — example.com, телефон — код 000.
Номера документов/карты — диагностические последовательности с нулевыми префиксами.
Checksum не подтверждает выдачу номера. Паспорт проверяется по контексту и форме;
ИНН рассматривается только физлица, длина 12 цифр.

PERSON объединяет полное имя и части: разделение классов — отдельный адаптер,
здесь он не реализован. [Таблица покрытия классов](../../docs/research/notes/pii-class-coverage.md)
показывает, какие примеры и типы реально проверены. SPECIAL_CATEGORY означает
упоминание сигнала, не юридический вывод. Отрицания извлекаются как упоминания;
истинность фактов, отзывы и принадлежность человеку здесь не оцениваются.

TP/FP/FN: совпадение типа и точных границ, дополнительно — любое пересечение
того же типа. Сопоставление взаимно однозначное. Тексты короткие и ручные;
частоты нерепрезентативны. Сессионный F2 и задержка в сообщениях не считаются.

Латенси: CPU, batch=1, три повтора после прогрева, threads из config; p95
nearest-rank по всем повторам. Загрузка и прогрев отдельно. В v2 combined
включает regex, GLiNER, конверсию адаптера и merge; в старом v1 конверсия не входила.
Это не HTTP-латенси и не нагрузочный тест. Совпадение скорости на другом железе
не обещается. Повторы должны давать одинаковые predictions, иначе run завершается
ошибкой. Межтиповые конфликты сохранены как свойство исследуемого baseline.
