# База знаний проекта

Точка входа. Хранитель базы знаний обновляет эту страницу раз в 2 недели.

## Проект

- [Общий конфиг обработчика и контекст LLM-разметчика](project/annotation-context-config-2026-11-18.md)

- [Рабочее правило: неполное имя и дополнительный личный атрибут](project/partial-name-alert-working-rule-2026-11-17.md)

- [Предложение для ТЗ: исследование частей имени и правило полного ФИО](project/fio-annotation-rule-proposal-2026-11-17.md)
- [Постановка задачи](project/brief.md)
- [Роадмап и чекпоинты](project/roadmap.md)
- [Команда и встречи](project/team.md)
- [Вопросы к ментору и команде](project/open-questions.md)
- [Глоссарий](project/glossary.md)
- [Уточнения политики первого алерта и принадлежности атрибутов](project/alert-policy-proposal-2026-11-13.md)
- [Адреса, телефоны и email: синтетические случаи для ТЗ](project/orange-attributes-examples-2026-11-15.md)
- [Уточнения оценщика: свежие данные, группы поселений, ФИО, hh и адреса](project/annotation-estimator-next-steps-2026-11-15.md)
- [Закрытие пробелов: рабочие решения и запуск калькулятора](project/annotation-gap-resolution-2026-11-15.md)

## Исследование

- [Статистические основания оценщика](research/notes/annotation-estimator-statistical-basis.md)

- [Средние для неизвестной численности пунктов, улиц и домов](research/notes/mvp-average-fallbacks-2026-11-17.md)

- [ФНС и СРО: отчества, совместные ИО и межисточниковая проверка](research/notes/fns-sro-patronymic-study-2026-11-17.md)

- [Источники отчеств и совместных наблюдений ИО/ФИО](research/notes/patronymic-sources-2026-11-17.md)

- [Достаточность корпуса VK для зависимости ФИ и порога алерта](research/notes/vk-annotation-evidence-2026-11-17.md)

- [API Одноклассников: доступ, права приложения и возможность корпуса ФИ](research/notes/ok-api-name-access-2026-11-17.md)

- [Викиданные: связи имени, фамилии, отчества и имени отца](research/notes/wikidata-name-father-2026-11-16.md)
- [Википедия: пилот явно написанных ФИО и зависимости частей](research/notes/wikipedia-explicit-fullnames-2026-11-16.md)
- [ФНС и Википедия: совместная ФИ и перенос на ФИО, локальный кеш](research/notes/fns-wiki-name-pairs-2026-11-16.md)
- [VK API: настройка доступа и пилот пары «имя — фамилия»](research/notes/vk-api-name-pairs-setup-2026-11-16.md)
- [VK: участники сообщества через groups.getMembers и локальный кеш](research/notes/vk-community-member-pairs-2026-11-16.md)

- [Москва, взрослые реестры и готовые корпуса: проверка назначения](research/notes/name-sources-moscow-registries-corpora-2026-11-16.md)

- [Расширение статистики ФИО по документам МГУ и ТГУ](research/notes/public-fullname-documents-batch-2026-11-16.md)

- [Пилот статистики ФИО по открытым документам и профилям](research/notes/public-fullname-source-pilot-2026-11-16.md)

- [Частоты ФИО: проверка источников и ошибка независимого произведения](research/notes/full-name-frequency-validation-2026-11-16.md)

- [Зависимости признаков, ФИО, малые населённые пункты и должности](research/notes/attribute-dependence-and-occupation-mapping-2026-11-15.md)
- [Источники](research/sources.md)
- [География и население России: источники и воспроизведение](research/notes/russia-geography-population-2026-11-15.md)
- [Статистика для правил разметки: региональные сайты, выгрузки и API](research/notes/statistics-sources-for-annotation-2026-11-15.md)
- [Реестр региональных источников Росстата и пробелов покрытия](research/notes/rosstat-all-regions-2026-11-15.md)
- [Рабочий справочник статистических оснований разметки](research/notes/annotation-statistics-handbook-2026-11-15.md)
- [Пол и частоты ФИО; запуск сборщика статистики hh](research/notes/sex-name-probabilities-and-hh-runbook-2026-11-15.md)
- [Выбранный источник профессиональной статистики: занятость Росстата](research/notes/rosstat-occupation-reference-2026-11-15.md)
- [Исследование PII-детекторов: результат, эксперимент и API](project/detector-research-summary.md)

## Знания (заполняются по мере ответов на вопросы)
- [ПДн и момент перехода сессии в ПДн](kb/pii-definition.md)
- [Линковка сущностей](kb/entity-linking.md)
- [Метрики](kb/metrics.md)
- [Открытые вопросы перед замером метрик](project/metrics-open-questions.md)
- [Проект ТЗ на разметку для метрик](data/metrics-annotation-requirements.md)
- [Бюджет латенси](kb/latency-budget.md)
- [Архитектура](kb/architecture.md)

## Данные

- [Как пользоваться ТЗ и запускать разметку](data/annotation-workflow.md)

- [Задание для основной LLM-разметки](data/llm-annotation-prompt.md)

- [Таблица полей по классам для LLM-разметки](data/annotation-class-fields.md)

- [Синтетические примеры MVP и разбор разногласий](data/annotation-synthetic-examples.md)
- [Инструкция по разметке](data/annotation-guidelines.md)
- [Предложенная версия правил первого алерта](data/annotation-guidelines-proposed-2026-11-15.md)
- [Формат данных](data/schema.md)
- [Карточка датасета](data/dataset-card.md)

## Журнал

- [Первый проход LLM-разметки синтетических сессий](experiments/2026-11-18-llm-annotation-pilot.md)

- [Средние численности пунктов и жилых домов: расчёт и предложения](experiments/2026-11-18-population-size-means.md)
- [Решения (ADR)](decisions/) · [Инсайты](insights/) · [Эксперименты](experiments/) · [Встречи](meetings/)
