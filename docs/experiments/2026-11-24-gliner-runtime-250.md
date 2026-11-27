# Эксперимент: локальная GLiNER и сессионный сервис на срезе Gemma

- Дата / автор: 2026-11-24; команда проекта.
- Задача: установить веса и повторить оценку доступного сервиса с настоящей GLiNER.
- Гипотеза: модель расширит покрытие regex; реальный запуск также проверит
  совместимость выходов адаптера с контрактом обработчика.

## Настройка

- Базовый коммит: `2172a36`; инструменты, исправление подготовки и документация
  поставляются вместе. Не связанные изменения worktree сохранены.
- Исходный эталон и выборка — как в [regex-эксперименте](2026-11-24-random-linker-250.md).
  Авторская разметка повторно использована по запросу пользователя; независимой
  человеческой проверки нет. Неоднозначные сценарные имена не переопределялись.
- Каталог: `data/experiments/random-linker-250-gliner-2026-11-24/`.
- Модель: `urchade/gliner_multi-v2.1`, revision
  `443d26d654e0324125a96bebd8e796c14ff2efe6`; backbone
  `microsoft/mdeberta-v3-base`, revision `a0484667b22365f84929a935b5e50a51f71f159d`.
- Конфиг: `benchmarks/pii_smoke/config.json`; порог и метки не подбирались по этому срезу.
  Риск: `configs/runtime-risk-policy.json`; эталонные алерты — отдельная
  политика разметки. Случайный линкер и seed сохранены от предыдущего эксперимента.
- Железо: локальная Windows x64, CPU. Версии Python и пакетов —
  `environment.json`, полный список — `freeze.txt`. Применён Python 3.11,
  отдельное окружение `.venv-gliner`; lock для Python 3.13 не использовался.
- Веса: `outputs/gliner-prepared/`. Хеши проверены; manifest скопирован
  в каталог результатов как `prepared-model-manifest.json`.

## Установка и воспроизведение

Из корня worktree `outputs/session-adapter-integration`:

```powershell
python -m venv .venv-gliner
.\.venv-gliner\Scripts\python.exe -m pip install --upgrade pip
.\.venv-gliner\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv-gliner\Scripts\python.exe -m pip install -r benchmarks/pii_smoke/requirements.txt -r requirements-service-test.txt
.\.venv-gliner\Scripts\python.exe -m pip check

$env:HF_HOME = Join-Path (Get-Location) '.cache-research/huggingface'
$env:HF_HUB_DISABLE_XET = '1'
Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
Remove-Item Env:TRANSFORMERS_OFFLINE -ErrorAction SilentlyContinue
.\.venv-gliner\Scripts\python.exe -X utf8 tools/download_gliner_ranges.py
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\.venv-gliner\Scripts\python.exe -X utf8 -m benchmarks.pii_smoke.prepare --output outputs/gliner-prepared
```

Загрузчик диапазонов понадобился из-за зависания большого GET. Он получает
URL из официального HF API, сверяет каждый диапазон и SHA-256 всего файла.
Подготовка затем использует локальный кеш. Подробнее —
[заметка о CDN](../insights/2026-11-24-gliner-cdn-ranges.md).

Реальный инференс и пересчёт метрик:

```powershell
.\.venv-gliner\Scripts\python.exe -X utf8 tools/evaluate_random_linker.py --directory data/experiments/random-linker-250-gliner-2026-11-24 --mode gliner --model-dir outputs/gliner-prepared --seeds 0 1 2 3 4
```

Быстрое воспроизведение чисел ниже по сохранённому реальному выходу без
повторной загрузки модели и инференса:

```powershell
.\.venv-gliner\Scripts\python.exe -X utf8 tools/evaluate_random_linker.py --directory data/experiments/random-linker-250-gliner-2026-11-24 --mode gliner --model-dir outputs/gliner-prepared --reuse-recordings
.\.venv-gliner\Scripts\python.exe -X utf8 tools/compare_gliner_runtime.py --regex data/experiments/random-linker-250-2026-11-24 --gliner data/experiments/random-linker-250-gliner-2026-11-24
```

Выход детектора сохраняется один раз на уникальный точный текст. Повторные
seed и HTTP получают эти реальные спаны, без эталонных сущностей и владельцев.
При повторном использовании проверяются отпечатки текстов, конфига и manifest.
Файл identity для первого завершённого инференса был добавлен после него
из тех же неизменённых текстов, конфига и подготовленной модели.

## Результаты

Приведённые выше команды воспроизводят таблицу и счётчики по сохранённым данным.
Корпус: 250 сессий, 718 сообщений, 90 положительных сессий. Модель выполнила
реальный инференс для 500 уникальных текстов без сети.

| Проверка | Regex | GLiNER + regex |
|---|---:|---:|
| Извлечение адаптера до фильтров, F2, все 250 сессий | 0.5013 | 0.5684 |
| Precision извлечения, все 250 сессий | 1.0000 | 0.5296 |
| Recall извлечения, все 250 сессий | 0.4457 | 0.5790 |
| Средний F2-timely, общий успешный срез из 222 сессий | 0.2444 | 0.2558 |
| Ошибки обработки сессий из 250 | 0 | 28 |

При наличии ошибок условный F2 на успешных сессиях нельзя выдавать за
качество сервиса на всём корпусе. Ошибки не засчитаны как безопасные TN.
Среднее считается по повторным seed на тех же данных, это не доверительный
интервал. Сравнение с regex ограничено теми же успешными сессиями; прежнее
среднее regex на всём корпусе нельзя напрямую сопоставлять с этим числом.

Для GLiNER после фильтров на успешном срезе: TP=260, FP=194, FN=201,
F2=0.5657. Это другой охват, чем метрика адаптера на полном корпусе.
Проверки линкера и риска с правильными входами остались независимыми от
модели: средний pairwise F1 линкера — 0.2729, F2-timely риска — 0.9825.
Сохранились ранее описанные ложные алерты на неизвестных контактах.

HTTP-прогон всех сессий на записанных модельных выходах подтвердил одинаковые
спаны, владельцев, первый алерт и позиции ошибок с прямым Pipeline.
Он проверяет API-обвязку; повторного реального инференса внутри HTTP нет.

Медиана реального инференса после первого вызова — около 306 мс, p95 — около
353 мс. Первый вызов с инициализацией — около 19.5 с. Это время детектора
на уникальных текстах, не нагрузочный тест и не HTTP-латенси. Время Pipeline
при повторном проигрывании кешированных спанов публикуется отдельно.

## Анализ ошибок

- Загрузка: нативный SentencePiece не открывал абсолютный путь с кириллицей.
  Подготовка теперь сохраняет стандартный fast tokenizer из того же словаря,
  не сохраняя флаг принудительной повторной slow-конвертации. Проверена
  идентичность ID токенов исходной конвертации и загрузки по полному пути.
  Веса и исходный SPM не менялись. [Разбор](../insights/2026-11-24-sentencepiece-unicode-path.md).
- Контракт EMAIL: 24 сессии отклонены. Например, в `synthetic-1000-0006`
  модель возвращает EMAIL на «Личная почта», а regex отдельно находит полный
  `owner2@example.invalid`. Pipeline валидирует каждый спан до разрешения
  пересечений, поэтому некорректный модельный кандидат приводит к HTTP 502.
- Контракт AGE: 4 сессии отклонены из-за возраста словами. Например,
  `synthetic-1000-0092`: модель выделяет «тридцать пять лет», тогда как
  текущий контракт допускает явно записанный числовой возраст. Этот пример
  остаётся отрицательным в текущем авторском сценарии; разметка не менялась.
- Извлечение: модель добавила правильные спаны, но также много лишних и
  несовпадающих по границам/типу. Сам факт загрузки checkpoint не подтверждает
  нужное качество адаптера. Порог по этому набору не подбирался.

## Проверки

```powershell
.\.venv-gliner\Scripts\python.exe -X utf8 -m unittest discover -s tests/service -p 'test_*.py'
.\.venv-gliner\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_download_gliner_ranges.py'
.\.venv-gliner\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_prepared_gliner_tokenizer.py'
```

## Вывод и следующий шаг

Пакеты, веса и tokenizer установлены; реальный CPU-инференс работает offline.
Следующая доработка адаптера — проверять модельные EMAIL и AGE по тому же
контракту, что использует обработчик, и не передавать явно невалидные кандидаты
как готовые сущности. Это предложение, а не внесённая правка правил.
Также остаются низкое качество случайного линкера и расхождение политики
неизвестных фактов с эталоном. Подробные результаты — `evaluation.json`,
`report.md`, `comparison-with-regex.json` и предсказания по seed в каталоге прогона.
