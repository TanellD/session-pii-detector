# Docker-сервис v2: сборка, API и тайминги

Ветка `feat/docker-service-v2`, отдельный worktree `outputs/docker-service-v2`.
Это интеграция существующих компонентов, не новая модель и не подтверждение
качества. HTTP/JSON сохранён как первый транспорт: он уже реализует сессии,
epoch, порядок сообщений и retry, а тот же интерфейс можно использовать для
нагрузочных профилей. gRPC пока не добавляется; сравнение транспортов имеет
смысл отдельным экспериментом после измерения затрат моделей и состояния.

## Компоненты

Поток: HTTP → состояние сессии → regex + GLiNER → RevisedAdapter v2 →
нормализация пересечений → линкер → RiskEvaluator → коммит состояния → ответ.

- По умолчанию Compose: regex + v2, линкер bypass, базовая политика runtime-risk-v2.
- GLiNER запускается отдельным Docker target и получает подготовленные веса read-only.
- Gemma использует прежний PROMPT и JSON-контракт, окно предыдущих сообщений и
  накопленные факты. Его ключ передаётся через окружение, не включается в образ.
- Исследовательский порог 0.35/0.60 доступен отдельным конфигом
  configs/runtime-risk-tuned-validation.json. По умолчанию не включён: подбор был
  предварительным на успешном подмножестве validation.
- Части имени и неразрешённые пересечения сохраняют известные ограничения v2;
  ошибки пересечений не скрываются заменой владельца.

Один процесс Uvicorn и одна реплика: состояние сессий в памяти, общего хранилища
нет. Перезапуск теряет сессии. Внутри сессии сообщения отправляются последовательно;
разные сессии могут обрабатываться конкурентно. До общего хранилища нельзя
масштабировать добавлением независимых workers/реплик и ожидать общей истории.

## Начальный запуск без внешнего линкера

Нужно получить исходники ветки `feat/docker-service-v2`, установить Docker Engine
с Compose (на Windows — Docker Desktop в режиме Linux containers) и иметь
доступ к интернету при сборке зависимостей. Запускать команды из корня репозитория.
Python на хосте для обычного Docker/Swagger-запуска не требуется.
Для начального regex-режима не нужны веса моделей и ключи API.

```powershell
git clone --branch feat/docker-service-v2 https://github.com/TanellD/session-pii-detector.git
cd session-pii-detector
docker version
docker compose version
```

Если исходники уже есть, переключиться на эту ветку в отдельном worktree или
чистом рабочем каталоге, не заменяя несохранённые локальные изменения.

Из корня нового worktree, при работающем Docker Engine:

```powershell
$env:PII_HOST_PORT='8020'
$env:PII_LINKER_MODE='bypass'
docker compose -p pii-docker-v2 up --build -d --wait
```

Swagger: http://127.0.0.1:8020/docs. API:

Здесь нет отдельного пользовательского сайта: `/docs` — интерактивная Swagger UI.
Открыть POST /v1/sessions, нажать Try it out и Execute:

```json
{"session_id": "synthetic-demo-01"}
```

Скопировать session_epoch из ответа. Затем выполнить POST /v1/analyze:

```json
{
  "session_id": "synthetic-demo-01",
  "session_epoch": "ЗАМЕНИТЬ_ЗНАЧЕНИЕМ_ИЗ_ОТВЕТА",
  "expected_state_version": 0,
  "message": {"message_id": 1, "role": "user", "text": "Моя почта demo@example.invalid."}
}
```

Для следующего сообщения брать state_version и next_message_id из последнего
успешного ответа. Точный повтор предыдущего запроса возвращает прежний результат;
неверная версия/порядок дают конфликт. Для повторной демонстрации использовать
новый session_id или reset с текущими epoch и версией. Использовать синтетические
примеры. Те же JSON можно отправлять HTTP-клиентом без Swagger.

| Путь | Назначение |
|---|---|
| GET /healthz | Процесс жив |
| GET /readyz | Startup и прогрев завершены; режим и параметры запуска |
| POST /v1/sessions | Создать сессию, получить epoch и версию |
| POST /v1/analyze | Обработать следующее сообщение с CAS |
| GET /v1/sessions/{session_id} | Версия и сводка сессии |
| POST /v1/sessions/{session_id}/reset | Сбросить состояние и epoch |

Readiness означает готовность выбранного режима, а не полноту семантического
анализа: regex или bypass остаются неполными. Порт Compose привязан к localhost;
аутентификация внешнего клиента/gateway здесь не реализована.

## GLiNER в контейнере

Для полного NER дополнительно нужен подготовленный каталог GLiNER с
model.safetensors, gliner_config.json, manifest.json и подкаталогом backbone.
Он хранится отдельно от git и не скачивается при старте сервиса. Можно получить
такой каталог из текущих экспериментальных артефактов либо подготовить публичные
веса и backbone по закреплённым ревизиям из benchmarks/pii_smoke/config.json.

Для подготовки с нуля нужен Python 3.12 и отдельное окружение на хосте:

```powershell
python -m venv .venv-model
.venv-model/Scripts/python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0
.venv-model/Scripts/python.exe -m pip install -r benchmarks/pii_smoke/requirements.txt
.venv-model/Scripts/python.exe -X utf8 benchmarks/pii_smoke/prepare.py --output outputs/gliner-prepared
```

На Linux использовать `.venv-model/bin/python`. Если каталог уже получен,
повторная загрузка не нужна. Для GLiNER выделить Docker память под модель и
приложение с запасом; доступные ресурсы влияют на прогрев. Первый build модельного
образа скачивает дополнительные CPU-зависимости и занимает больше времени.

Подготовленный модельный каталог содержит абсолютный model_name backbone.
Копия из Windows без изменения этого пути не переносима в Linux. Создать
отдельную копию; исходный каталог экспериментов и его manifest не меняются:

```powershell
.venv-model/Scripts/python.exe -X utf8 tools/relocate_prepared_model.py --source outputs/gliner-prepared --output outputs/gliner-docker
docker volume create pii-docker-service-v2-gliner-model
$modelHostDirectory = (Resolve-Path outputs/gliner-docker).Path
docker run --rm --user 0 --mount "type=bind,source=$modelHostDirectory,target=/source,readonly" --mount 'type=volume,source=pii-docker-service-v2-gliner-model,target=/target' --entrypoint python session-pii:service-v2-runtime -c "from pathlib import Path; import shutil; t=Path('/target'); assert not any(t.iterdir()), 'Model volume is not empty'; shutil.copytree('/source',t,dirs_exist_ok=True)"
docker compose -p pii-docker-v2 -f compose.yaml -f compose.gliner.yaml up --build -d --wait --wait-timeout 240
```

Runtime-путь копии — /models/gliner; `PII_MODEL_VOLUME` позволяет выбрать другой
заранее заполненный volume. Docker Desktop читает веса с Linux-файловой системы,
а не через Windows bind-mount. Одноразовый helper заполняет пустой volume от root;
сам сервис остаётся под UID 10001, volume подключается read-only. Если volume
уже заполнен, не перезаписывать его: проверить версию или выбрать новое имя.
Копирование модели из исходного каталога проверяет хеши,
переписывает только конфигурацию копии и формирует новый manifest с происхождением.
Большие safetensors могут использовать hardlink: не изменять файлы весов вручную.
Исходная подготовка весов описана в benchmarks/pii_smoke/prepare.py.

На старте GLiNER загружается и выполняет прогрев до готовности API. Runtime
работает offline, без загрузки модели из интернета. Сборка образа требует сети
для зависимостей. Direct dependency pins совпадают с исследованием; полный
lock транзитивных Linux-зависимостей ещё не зафиксирован.
Источник механизма offline: [документация GLiNER](https://github.com/urchade/GLiNER/blob/main/docs/usage.md).

## Линкер и fallback

Для быстрой проверки изменений кода поверх уже собранных зависимостей можно
добавить последним `-f compose.dev.yaml`: он монтирует текущий src/session_pii
read-only. При изменении кода пересоздать контейнер, чтобы перезапустить процесс.
Этот overlay предназначен для разработки; без него код берётся только из образа.
В отчёте указывать, использован ли overlay и какой commit исходников подключён.

| Настройка | Поведение |
|---|---|
| PII_LINKER_MODE=bypass | Никакого внешнего вызова; все владельцы неизвестны, coverage_complete=false |
| PII_LINKER_MODE=gemma | Настоящий Gemma JSON-линкер |
| PII_LINKER_MODE=stub или random | Старые контрольные режимы |
| PII_LINKER_FALLBACK=error | По умолчанию: ошибка линкера даёт HTTP 502, состояние не коммитится |
| PII_LINKER_FALLBACK=unknown | При ошибке продолжить с неизвестными владельцами и linker_fallback_unknown |
| PII_BENCHMARK_LINKER_DELAY_MS=0 | Искусственная задержка только bypass; это не реальный инференс |
| PII_LINKER_TIMEOUT_SECONDS=90 | Таймаут ожидания места и сетевого обращения Gemma |
| PII_LINKER_CONCURRENCY=1 | Число одновременных обращений Gemma на процесс |

Таймаут очереди и сетевой timeout — отдельные ограничения; это не жёсткий
deadline всего HTTP-запроса. Контекст и парсинг входят в время стадии linker.
Автоматических сетевых retry нет. Bypass не является аварийным fallback:
его выбирают заранее для нагрузочного замера без API линкера.

При fallback или bypass полнота сессии остаётся потерянной до reset. Неизвестные
факты всё равно участвуют в своих правилах риска: session_alert может стать true,
но отсутствие алерта при неполноте нельзя считать разрешением пропустить данные.
Позднее уточнение владельца прежнего факта по-прежнему не переназначает его автоматически.

Для настоящего линкера загрузить RADEON_API_KEY из локального .env или окружения,
затем выбрать PII_LINKER_MODE=gemma. Сам .env не копировать в образ и не логировать.
В локальном .env достаточно строк RADEON_API_KEY со своим ключом и
PII_LINKER_MODE=gemma, затем пересоздать контейнер той же командой Compose.
Если ранее в PowerShell задан `$env:PII_LINKER_MODE='bypass'`, заменить его на
`$env:PII_LINKER_MODE='gemma'`: окружение терминала имеет приоритет над .env.
Для воспроизводимости фиксировать модель/промпт/окно из configs/linker-gemma-baseline.json.

Выбор политики-кандидата (явно, до создания новых сессий):

```powershell
$env:PII_RISK_POLICY='/app/configs/runtime-risk-tuned-validation.json'
docker compose -p pii-docker-v2 up -d --force-recreate
```

Для модельного режима при пересоздании сохранять оба `-f` файла Compose.

## Логи и времена

Приложение пишет JSON в stdout: startup, model_load_start/complete,
model_warmup_start/complete, http_request, analysis_result,
component_error, linker_bypassed, linker_fallback. Внутри записи — случайный
request_id, шаблон маршрута, статус, число сущностей, полнота, булев алерт и времена.
Не пишутся тексты, значения сущностей, session_id/epoch, заголовки авторизации,
ключи или текст исключения. Входной X-Request-ID не принимается на доверие:
сервер выдаёт собственный ID и возвращает его в ответе.

`Server-Timing` содержит стадии ner, regexp, normalization, linker, risk и analysis;
Gemma дополнительно linker_wait/linker_external. Нулевое значение regexp при
объединённом адаптере ожидаемо: regex уже входит в ner. Таймер ner включает
ожидание сериализованной локальной модели. Analysis включает доступ к состоянию,
ожидание блокировки сессии и обработку; HTTP middleware дополнительно включает
ожидание пула обработчиков и создание ответа. Клиентский round-trip измеряется
отдельно и включает сеть.

analysis_excluding_linker_ms — analysis минус всю стадию linker, а не полный
формальный бюджет локального алгоритма. Внешний LLM включён в HTTP/linker;
загрузка модели исключена из прогретого замера благодаря readiness.
Названия и границы сопоставлять с docs/kb/latency-budget.md.

`PII_LOG_SAMPLE_RATE=1` логирует все успешные запросы; можно задать долю 0–1.
Ошибки и fallback не семплируются. Долю фиксировать в отчёте: логирование само
влияет на нагрузку. Docker json-file ротируется, настройки в compose.yaml.
Служебные строки Uvicorn/библиотек при startup не обязаны иметь JSON-формат.

```powershell
docker compose -p pii-docker-v2 logs -f pii-service
```

## Подготовка к внешним замерам

Генератор нагрузки в эту поставку не входит. HTTP API, Server-Timing и JSON-логи
сохранены: отдельный клиент может собирать задержки запросов и стадий. Внутри
сессии сообщения отправляются последовательно, разные сессии могут идти параллельно.
Байпас не проверяет качество владельцев, а HTTP 200 не означает полный анализ.

PII_MAX_INFLIGHT ограничивает одновременную работу; переполнение analyze даёт
HTTP 503 OVERLOADED. PII_TORCH_THREADS управляет CPU модели. При замерах фиксировать
ресурсы, режимы компонентов, политику, конкуренцию, долю логов и ошибки.
Накопленные сессии ограничены PII_MAX_SESSIONS и TTL.

Остановить проект без удаления модельного volume:

```powershell
docker compose -p pii-docker-v2 down
```

Для модельного режима использовать те же `-f` файлы, что при запуске.

## Проверки

```powershell
python -X utf8 -m unittest discover -s tests/service -q
docker compose config --quiet
docker compose -f compose.yaml -f compose.gliner.yaml config --quiet
```

Результаты текущих проверок и ограничения Docker-сборки:
[эксперимент интеграции](../experiments/2026-11-26-docker-service-v2.md).
