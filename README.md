# Session PII — сессионная детекция ПДн

Модуль, который при каждом новом сообщении пользователя анализирует **всю сессию** и находит момент, когда набор по отдельности безобидных сигналов становится утечкой персональных данных (ФЗ-152).

Рабочая версия правил разметки — `annotation-mvp-v2`. ФИО само по себе не вызывает алерт: применяются согласованные сочетания и численные правила. Пилот разметки не является готовым детектором или доказательством его качества.

**Основное ТЗ: [docs/data/annotation-guidelines.md](docs/data/annotation-guidelines.md). Как пользоваться: [пошаговая инструкция](docs/data/annotation-workflow.md).**

## Критерий успеха
1. Собран набор диалогов с разметкой **момента**, когда сессия становится ПДн.
2. Модуль ловит этот момент: **точность по сессиям** и **задержка в сообщениях**.
3. Время обработки нового сообщения укладывается в **бюджет латенси** по мере роста сессии.

Подробно: [docs/project/brief.md](docs/project/brief.md) · метрики: [docs/kb/metrics.md](docs/kb/metrics.md)

## Навигация
| Что | Где |
|---|---|
| Вход в базу знаний | [docs/index.md](docs/index.md) |
| ТЗ разметки | [Правила MVP](docs/data/annotation-guidelines.md) |
| Установка и работа с ТЗ | [Инструкция для команды](docs/data/annotation-workflow.md) |
| Классы и границы сущностей | [Таблица полей и примеров](docs/data/annotation-class-fields.md) |
| Формат аннотации | [JSON-контракт](docs/data/schema.md) |
| Задание для LLM | [Промпт](docs/data/llm-annotation-prompt.md) |
| Правила и коэффициенты | [Общий конфиг](configs/annotation-estimator-policy.json) |
| Синтетические диалоги | [Примеры и разбор разногласий](docs/data/annotation-synthetic-examples.md) |
| Первый проход LLM | [Отчёт и ограничения](docs/experiments/2026-11-18-llm-annotation-pilot.md) |
| Статистика населённых пунктов и домов | [Расчёт средних](docs/experiments/2026-11-18-population-size-means.md) |
| Исследование PII-детекторов и API | [Результат исследования](docs/project/detector-research-summary.md) |
| План и чекпоинты | [docs/project/roadmap.md](docs/project/roadmap.md) |
| Открытые вопросы (к ментору и команде) | [docs/project/open-questions.md](docs/project/open-questions.md) |
| Как мы работаем | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Правила для AI-агентов | [AGENTS.md](AGENTS.md) |
| Презентации и публикация | [deliverables/](deliverables/) |

## Статус
Исследование и подготовка корпуса: есть рабочее ТЗ, обработчик правил, статистические исследования и синтетический LLM-пилот. Частоты ФИО остаются исследовательскими; средние улиц не подтверждены; подготовленные новые средние ещё не подключены к оценщику. Прохождение примеров не измеряет качество модели.

## Быстрый старт
Python 3.11+, из корня проекта, Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r tools/requirements-annotation.txt
.venv\Scripts\python.exe -X utf8 tools/build_llm_annotation_pilot.py --policy-only --output data/experiments/local-demo
.venv\Scripts\python.exe -X utf8 tools/annotation_contract.py --policy-only --check-cases tests/fixtures/annotation-mvp-cases.json
.venv\Scripts\python.exe -X utf8 tools/export_annotation_context.py --output data/reference/annotation-llm-context.json
```

На Linux/macOS используйте `.venv/bin/python`. Ключи и скачивание данных для демо не нужны. В `data/experiments/local-demo/` появятся исходные сессии, сохранённые аннотации, результаты обработчика и манифест. Это воспроизведение сохранённого LLM-прохода, не новая генерация.

Режим `--policy-only` использует правила и коэффициенты конфига без локальных файлов справочников; отсутствующая статистика остаётся неизвестной. Полный численный режим и проверка своей сессии описаны в [инструкции](docs/data/annotation-workflow.md).

Браузерный калькулятор: `.venv\Scripts\python.exe -X utf8 tools/annotation_calculator.py --policy-only`, затем откройте `http://127.0.0.1:8765`. Он проверяет одну JSON-аннотацию или позволяет выбрать сессию из `in-session-annotations.json`; есть также ручной ввод полных атрибутов. Для подключённых локальных справочников запускайте без `--policy-only`.

Калькулятор также [оценивает жителей по строке адреса](docs/project/address-population-estimator.md): квартира — по среднему, МКД — по числу квартир, индивидуальный дом — при подтверждённом типе. Для поиска нужен локальный ключ DaData; средние жилья и работодателя заданы в конфиге.

## Данные и ключи

В Git хранятся код, конфиги, документация и синтетические регрессионные примеры. Содержимое `data/`, выгрузки, результаты прогонов и `.env` не публикуются. Реальные ПДн запрещены: [ADR-0002](docs/decisions/0002-no-real-pii.md).

Для демо `.env` не нужен. Для внешних API создайте его из `.env.example`, если файла ещё нет, и заполните нужный ключ только локально. Отдельный LLM API-проход ещё не выполнен. Сборщики VK/ОК и поиск адреса не запускаются при установке или выполнении демо.

Изменения ТЗ, кода и примеров рассматриваются в одном PR; для нового корпуса фиксируются версия инструкции, хеш политики и статус проверки человеком. Публикация ветки не означает выпуск датасета.

## Лицензия
На выбор: [MIT](LICENSE-MIT) или [Apache-2.0](LICENSE-APACHE).
