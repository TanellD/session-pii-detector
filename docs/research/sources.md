# Источники

Одна строка на источник. Подробный конспект — в `notes/`.

| # | Источник (ссылка) | Тип | Тема | Главное для нас | Кто читал | Важность (1–3) |
|---|---|---|---|---|---|---|
| 1 | ФЗ-152 «О персональных данных» | закон | определение ПДн | | | |

## Задача #12 — проверено 02.11.2026

Идентификаторы `T12-*` независимы от `S*` в открытом PR #16. Источники —
публикации авторов, документация и страницы разработчиков. Маркетинговое заявление
не считается независимым замером. Подробности: [обзор](notes/pii-detectors-comparison.md).

| # | Источник | Тип | Главное для нас | Кто читал | Важность |
|---|---|---|---|---|---|
| T12-01 | [Lee et al., 2026](https://doi.org/10.3390/app16083680), [полный текст](https://www.researchgate.net/publication/403667723_Early_Detection_of_Re-Identification_Risk_in_Multi-Turn_Dialogues_via_Entity-Aware_Evidence_Accumulation) | статья авторов | Прямой аналог сессионного накопления. [Разбор](notes/dialogue-reidentification-paper.md). MDPI отвечал 429, текст прочитан в копии публикации на ResearchGate | `Kopylov-Andrey` | 1 |
| T12-02 | [GLiNER](https://github.com/urchade/GLiNER), [gliner_multi-v2.1](https://huggingface.co/urchade/gliner_multi-v2.1) | код / model card | Локальный label-conditioned NER; multilingual и Apache-2.0 у выбранных весов. Русское качество проверяем своим прогоном; не переносим рекламные обещания на конкретный checkpoint | `Kopylov-Andrey` | 1 |
| T12-03 | [Presidio Analyzer](https://presidio.dataprivacystack.org/analyzer/), [REST API](https://presidio.dataprivacystack.org/api-docs/api-docs.html) | документация | Модульный анализатор и стандартный результат entity_type/start/end/score. Русский набор распознавателей нужно явно собирать | `Kopylov-Andrey` | 1 |
| T12-04 | [Natasha](https://github.com/natasha/natasha) | код / документация | Русские PER/ORG/LOC, CPU, MIT. Модели оптимизированы для новостей; перенос на чаты не доказан | `Kopylov-Andrey` | 2 |
| T12-05 | [NeMo PII detection](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/pii-detection), [Presidio integration](https://docs.nvidia.com/nemo/guardrails/v0.24.1/configure-guardrails/guardrail-catalog/third-party/presidio) | документация | PII rails для input/output/retrieval; подключает готовые детекторы. Наличие rails не подтверждает накопление риска по лицам | `Kopylov-Andrey` | 2 |
| T12-06 | [Amazon Comprehend PII](https://docs.aws.amazon.com/comprehend/latest/dg/pii.html), [API](https://docs.aws.amazon.com/comprehend/latest/APIReference/API_DetectPiiEntities.html) | документация | Облачный анализ текста; PII только English/Spanish. Не кандидат для локального русского MVP, полезен как пример API | `Kopylov-Andrey` | 2 |
| T12-07 | [HiveTrace](https://hivetrace.ai/), [официальный SDK](https://github.com/HiveTrace/hivetrace-sdk) | продукт / код | Российский заказчик: DataClean, маскирование, on-premise. Публичные описания не доказывают entity-level onset. Точный внутренний NER API не получен | `Kopylov-Andrey` | 1 |
| T12-08 | [Solar AI Firewall, анонс 28.10.2026](https://rt-solar.ru/events/news/7158/) | первичный материал вендора | Российский шлюз: проверка запросов, маскирование, интеграция с Dozor. Алгоритм накопления косвенных признаков не раскрыт | `Kopylov-Andrey` | 2 |
| T12-09 | [HiveTrace GLiNER Guard Omni](https://huggingface.co/hivetrace/gliner-guard-omni) | model card | English/Russian, Apache-2.0, GLiNER2. Кандидат для следующего сравнения; API отличается от GLiNER v1, checkpoint в нашем прогоне не использован | `Kopylov-Andrey` | 1 |
| T12-10 | [brikkoAI/presidio-ru-recognizers](https://github.com/brikkoAI/presidio-ru-recognizers) | код разработчика | Русские форматные распознаватели, контрольные суммы ИНН/СНИЛС. Код не запускался и независимо не аудировался | `Kopylov-Andrey` | 2 |

### Сводная таблица аналогов и детекторов

Подробные критерии и ограничения — в [сравнении](notes/pii-detectors-comparison.md).
Неподтверждённая функция не считается отсутствующей во всём продукте.

| Решение | Класс | Сессионное накопление по людям | Основание |
|---|---|---|---|
| Lee et al. | Научный аналог | Описано | T12-01 |
| GLiNER / HiveTrace Guard Omni | Детекторы | Не описано в model cards | T12-02, T12-09 |
| Presidio / Natasha | Извлечение / NLP | Не описано в рассмотренных API | T12-03, T12-04 |
| NeMo Guardrails | Интеграционный framework | PII rails сами по себе не подтверждают | T12-05 |
| Amazon Comprehend | Облачный PII API | Не описано в PII API; EN/ES | T12-06 |
| HiveTrace DataClean | Российский продукт | Публично не раскрыто | T12-07 |
| Solar AI Firewall | Российский шлюз | Публично не раскрыто | T12-08 |
| Brikko recognizers | Русские правила | Не описано | T12-10 |
