# Автоматический отчёт CPU smoke-проверки #12

Дата запуска (UTC): `2026-11-02T20:37:58.337158+00:00`.
Автор: `Kopylov-Andrey`. Измерение извлечения сущностей; HTTP-сервис не запускался.

## Протокол

- Коммит: `d5ab66b10b7fbc9ce921aa79b19fd2b41c9ef63f`; dirty: `False`.
- Набор: `task12-smoke-v2`; фраз: 30.
- Dataset SHA-256: `ee84e967e5f20f2ac7188cd4fcebfb8848eb2b0e37b3469664389be0e11444b5`.
- Config SHA-256: `bb84bac2b8074b3d487eb20c3f2dfb4c3efe415b92aad940a22bc513654dc1e8`.
- Python: `3.13.0`; платформа: `Windows-10-10.0.19045-SP0`.
- CPU: `AMD Ryzen 5 5600G with Radeon Graphics`; логических потоков: 12.
- RAM: 15.79 GiB; свободно в начале: 2.13 GiB.
- Device: `cpu`; повторы: 3; batch=1.
- Пакеты: `torch==2.11.0+cpu`, `gliner==0.2.24`, `transformers==4.57.6`, `huggingface-hub==0.36.0`, `psutil==7.2.2`, `truststore==0.10.5`.
- Модель: `urchade/gliner_multi-v2.1` @ `443d26d654e0324125a96bebd8e796c14ff2efe6`.
- Backbone: `microsoft/mdeberta-v3-base` @ `a0484667b22365f84929a935b5e50a51f71f159d`.
- Torch threads: 2; threshold: 0.5.
- Загрузка: 9.613 s; прогрев: 0.530 s, вне латенси.
- Lock SHA-256: `ce6b79bf644ab8b6fee9fccc6f28e7b7d86a43a33007120ddb2a4c562644f6b7`.

## Результаты

Числа пересчитаны из сырых predictions и latency_ms; p95 — nearest-rank по всем повторам.

| Режим | TP / FP / FN, точные границы | TP / FP / FN, пересечение | Median, ms | p95, ms | Измерений |
|---|---|---|---|---|---|
| regex | 7 / 0 / 25 | 7 / 0 / 25 | 0.019 | 0.104 | 90 |
| gliner | 19 / 12 / 13 | 21 / 10 / 11 | 252.759 | 286.472 | 90 |
| combined | 25 / 11 / 7 | 26 / 10 / 6 | 252.789 | 286.495 | 90 |

## По типам: combined, точные границы

| Тип | TP | FP | FN |
|---|---|---|---|
| ADDRESS | 1 | 0 | 0 |
| AGE | 1 | 0 | 0 |
| CARD | 1 | 0 | 0 |
| CITY | 1 | 1 | 0 |
| DATE_OF_BIRTH | 1 | 0 | 0 |
| EMAIL | 2 | 0 | 0 |
| GENDER | 1 | 0 | 0 |
| INN | 1 | 0 | 0 |
| OCCUPATION | 0 | 0 | 2 |
| ORGANIZATION | 1 | 2 | 1 |
| PASSPORT | 1 | 0 | 0 |
| PERSON | 7 | 6 | 1 |
| PHONE | 1 | 2 | 0 |
| RELATIONSHIP | 0 | 0 | 1 |
| SNILS | 1 | 0 | 0 |
| SPECIAL_CATEGORY | 5 | 0 | 2 |

## Проверенные случаи: combined

| ID | TP | FP | FN |
|---|---|---|---|
| full_name | 1 | 0 | 0 |
| first_name | 0 | 1 | 1 |
| surname | 1 | 0 | 0 |
| phone | 1 | 1 | 0 |
| email | 1 | 0 | 0 |
| address | 1 | 0 | 0 |
| birth_date | 1 | 0 | 0 |
| age | 1 | 0 | 0 |
| city | 1 | 1 | 0 |
| organization | 0 | 3 | 1 |
| occupation | 0 | 1 | 1 |
| relationship | 0 | 1 | 1 |
| gender | 1 | 0 | 0 |
| health | 1 | 0 | 0 |
| religion | 1 | 0 | 0 |
| politics | 1 | 1 | 0 |
| nationality | 1 | 0 | 0 |
| philosophy | 1 | 1 | 0 |
| intimacy | 0 | 0 | 1 |
| conviction | 0 | 0 | 1 |
| snils | 1 | 0 | 0 |
| inn | 1 | 1 | 0 |
| card | 1 | 0 | 0 |
| passport | 1 | 0 | 0 |
| bad_checksum | 0 | 0 | 0 |
| patronymic | 1 | 0 | 0 |
| code | 1 | 0 | 0 |
| two_people | 3 | 0 | 1 |
| correction | 2 | 0 | 0 |
| negative | 0 | 0 | 0 |

## Расхождения на синтетических фразах

- `first_name`: Меня зовут Лада.
  Gold: PERSON: Лада. Predictions: CITY: Лада.
- `phone`: Мой телефон +7 000 000-00-01.
  Gold: PHONE: +7 000 000-00-01. Predictions: PHONE: Мой телефон; PHONE: +7 000 000-00-01.
- `city`: Я живу в Кимрах.
  Gold: CITY: Кимрах. Predictions: PERSON: Я; CITY: Кимрах.
- `organization`: Я работаю в ООО «Макет».
  Gold: ORGANIZATION: ООО «Макет». Predictions: PERSON: Я; ORGANIZATION: ООО; ORGANIZATION: Макет.
- `occupation`: Я работаю инженером.
  Gold: OCCUPATION: инженером. Predictions: PERSON: инженером.
- `relationship`: Моя мама скоро приедет.
  Gold: RELATIONSHIP: мама. Predictions: PERSON: Моя мама.
- `politics`: Я придерживаюсь социалистических взглядов.
  Gold: SPECIAL_CATEGORY: социалистических взглядов. Predictions: PERSON: Я; SPECIAL_CATEGORY: социалистических взглядов.
- `philosophy`: Я сторонница стоицизма.
  Gold: SPECIAL_CATEGORY: стоицизма. Predictions: PERSON: Я; SPECIAL_CATEGORY: стоицизма.
- `intimacy`: Это сведения о моей интимной жизни.
  Gold: SPECIAL_CATEGORY: интимной жизни. Predictions: нет.
- `conviction`: У меня была судимость.
  Gold: SPECIAL_CATEGORY: судимость. Predictions: нет.
- `inn`: Тестовый ИНН: 000000123411.
  Gold: INN: 000000123411. Predictions: INN: 000000123411; PHONE: 000000123411.
- `two_people`: Лада Макетова работает в ООО «Макет», а Олег Тестов — инженер.
  Gold: PERSON: Лада Макетова; ORGANIZATION: ООО «Макет»; PERSON: Олег Тестов; OCCUPATION: инженер. Predictions: PERSON: Лада Макетова; ORGANIZATION: ООО «Макет»; PERSON: Олег Тестов.

## Границы измерения

Это не сессионный F2. PERSON объединяет ФИО и части; классификация частей, линковка людей и истинность фактов не оценивались.
Набор мал и синтетический, частоты не репрезентативны. Повторы не являются независимыми фразами; любое пересечение — мягкая диагностика границ.
Латенси коротких текстов не включает HTTP, сеть, нагрузку и хранение состояния. Протокол combined указан в README и metadata; старый v1 исключал конверсию адаптера.
Ручные выводы относятся к указанному run и оформляются отдельно. Изменение числа ошибок само по себе не устанавливает их причину.
