# Проверка regex + GLiNER для #12

Диагностика извлечения сущностей, не реализация сессионного сервиса и не замер
проектного F2. Конфиг и ожидания фиксируются до первого прогона модели.
Порог не подбирается по этим примерам. Другие детекторы здесь не измеряются.

## Воспроизведение в PowerShell из корня репозитория

На исходной машине Python 3.13 и PyTorch уже установлены. Окружение использует
системный PyTorch, но добавленные библиотеки устанавливаются только в него.
На чистой машине сначала нужен PyTorch с поддержкой CPU; точные версии окружения
сохраняются в `runs/task12-smoke/freeze.txt` и описании эксперимента.

```powershell
py -3.13 -m venv --system-site-packages --without-pip .venv-research
.\.venv-research\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -r benchmarks/pii_smoke/requirements.txt
$env:HF_HOME = Join-Path (Get-Location) '.cache-research\huggingface'
$env:HF_HUB_DISABLE_XET = '1'
Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
Remove-Item Env:TRANSFORMERS_OFFLINE -ErrorAction SilentlyContinue
.\.venv-research\Scripts\python.exe -m benchmarks.pii_smoke.prepare
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\.venv-research\Scripts\python.exe -m benchmarks.pii_smoke.dataset
.\.venv-research\Scripts\python.exe -m unittest discover -s tests -p 'test_pii_smoke.py'
.\.venv-research\Scripts\python.exe -m benchmarks.pii_smoke.run
.\.venv-research\Scripts\python.exe -m pip freeze | Set-Content -Encoding utf8 runs/task12-smoke/freeze.txt
```

Для отдельной проверки правил: тот же `run` с `--regex-only --output runs/task12-regex`.
Подготовка скачивает публичные веса. Прогон работает offline, только на CPU.
В Windows `prepare` использует доверенное хранилище сертификатов через pip;
TLS-проверка не отключается. Окружение должно содержать pip (доступен системный).

## Набор и ограничения

Генератор создаёт синтетические фразы и gold spans в игнорируемом `data/`.
Имена и организации вымышлены, email — `example.com`, телефон — с невыделенным
кодом `000`; номера документов и карты — диагностические последовательности
с нулевыми префиксами. Контрольная сумма не подтверждает выдачу номера.
Паспорт проверяется только по контексту и форме; универсальной контрольной суммы
в этом прототипе нет. ИНН рассматривается только физлица, длина 12 цифр.

`PERSON` объединяет полное имя и части. Их разделение — отдельный адаптер,
проверка не доказывает готовность всех классов MVP. SPECIAL_CATEGORY — извлечение
сигнала, не вывод о наличии ПДн или правонарушении. Отрицания извлекаются как
упоминания, истинность фактов и принадлежность человеку здесь не оцениваются.

Считаем TP/FP/FN по типу и точным границам, дополнительно — любое пересечение
при совпадающем типе. Второй критерий более мягкий и показывается отдельно.
Сопоставление взаимно однозначное. Тексты короткие и подобраны вручную;
частотность классов не репрезентативна. Сессионный F2 и задержка в сообщениях
не считаются. Равенство прогонов не гарантируется на другом железе.

Латенси — повторы после прогрева, batch size 1, CPU, число потоков из конфига.
Медиана и p95 nearest-rank считаются по всем повторам; загрузка и прогрев отдельно.
Скорость combined включает правила, GLiNER и слияние. Это не API-латенси,
не нагрузочный тест, не доказательство постоянной стоимости длинной сессии.
