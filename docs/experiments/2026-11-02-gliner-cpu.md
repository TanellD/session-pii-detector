# Эксперимент #12: чистое окружение и покрытие ФИО, v2

- Дата / автор: 02.11.2026, `Kopylov-Andrey`.
- Issue: #12; исправления [аудита](../insights/archive/2026-11-02-research-audit.md).
- Гипотеза: закреплённые зависимости и локальный backbone позволяют повторить
  CPU-проверку без системных библиотек/кеш-lookup main; общий PERSON стоит проверить
  на трёхкомпонентном ФИО и отдельном отчестве.

## Настройка

- Код: `d5ab66b10b7fbc9ce921aa79b19fd2b41c9ef63f`, dirty=False.
- Данные: `task12-smoke-v2`, SHA-256
  `ee84e967e5f20f2ac7188cd4fcebfb8848eb2b0e37b3469664389be0e11444b5`.
  Тридцать фраз, 32 gold-упоминания. v1 сохранён; в v2 заменены два случая,
  labels/порог не менялись. Это проверка пробела покрытия, не новый blind test.
- Конфиг: `benchmarks/pii_smoke/config.json`, SHA-256
  `bb84bac2b8074b3d487eb20c3f2dfb4c3efe415b92aad940a22bc513654dc1e8`.
- Полное окружение: Windows x64, CPython 3.13.0, CPU torch 2.11.0+cpu,
  [lock](../../benchmarks/pii_smoke/requirements-win-py313.lock.txt).
  Установка из lock проверена во втором пустом venv, без system-site-packages.
  Lock SHA-256: `ce6b79bf644ab8b6fee9fccc6f28e7b7d86a43a33007120ddb2a4c562644f6b7`.
- Hardware: Ryzen 5 5600G, 12 логических CPU, RAM 15.79 GiB. CPU inference,
  torch threads=2, batch=1, repeats=3, threshold=0.5.
- Модель и backbone загружены по revisions из config; локальная копия содержит
  manifest с hashes. Загрузка strict=True, HF/Transformers offline, отдельный
  пустой HF_HOME. Проверялась независимость inference от кеша, не повторная
  загрузка всех весов через сеть в пустой кеш.

Команды подготовки — в [README](../../benchmarks/pii_smoke/README.md). Числа ниже
получаются из сохранённого run следующей командой:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-smoke-v2
```

Для нового измерения после подготовки окружения и offline-переменных:

```powershell
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.run --output runs/task12-smoke-v2-replay
.\.venv-task12-locked\Scripts\python.exe -m benchmarks.pii_smoke.report --input runs/task12-smoke-v2-replay
```

## Результаты

| Режим | TP / FP / FN, точные границы | CPU median / p95, ms |
|---|---|---|
| regex | 7 / 0 / 25 | 0.019 / 0.104 |
| GLiNER | 19 / 12 / 13 | 252.759 / 286.472 |
| combined | 25 / 11 / 7 | 252.789 / 286.495 |

По 90 warmed-измерений на режим — три повтора каждой фразы. В combined входят
regex, модель, конверсия адаптера и merge. Загрузка 9.613 s и прогрев 0.530 s
в латенси не включены. Сессионный F2, линковка, нагрузка и длинные тексты не измерены.
Команда report выше проверяет агрегаты и латенси, затем записывает подробные
расхождения в `runs/task12-smoke-v2/report.md`. Этот генерируемый файл хранится
вместе с прогоном вне Git. [Покрытие классов](../research/notes/pii-class-coverage.md).

## Анализ ошибок

1. `full_name`: «Лада Олеговна Макетова» и `patronymic`: «Олеговна» извлечены
   целыми PERSON. Это не проверка FULL_NAME/PATRONYMIC: разделяющего адаптера нет.
2. `first_name`: «Лада» получила CITY. Один успешный полный span не доказывает
   устойчивое извлечение частей имени.
3. `occupation` и `relationship`: «инженером» / «Моя мама» получили PERSON,
   нужные типы не найдены. `organization` разбита на ООО и Макет, что портит границы.
4. `inn`: checksum-правило нашло INN, модель добавила PHONE на те же цифры.
   Простейший merge разных типов намеренно не скрывает конфликт baseline.
5. `intimacy` и `conviction`: специальный сигнал пропущен. На других фразах
   встречаются ложные PERSON на местоимениях. Причины ошибок здесь не установлены.

## Вывод и следующий шаг

Чистая установка и offline-загрузка закреплённых артефактов работают. Расширение
набора закрыло отсутствие примеров полного ФИО и отчества; оно не превратило
generic PERSON в классификатор всех частей. Baseline остаётся исследовательским.
Результаты v1/v2 не сравниваем как улучшение модели: изменены данные, зависимости
и границы замера combined, а скорость зависит от фона машины.

При загрузке сохраняются предупреждения Transformers о byte fallback, regex
токенизатора и max_length. strict=True проходит; причинной связи предупреждений
с FP/FN не установлено. Эквивалентность fast/slow и длинные тексты требуют
отдельного эксперимента на этапе выбора production-детектора.

Следующий этап после #12: согласование классов/метрик, адаптеры, независимый набор
и сравнение Guard Omni / Natasha. Полный HTTP-сервис и сессионный F2 не входят
в исправляемый исследовательский smoke-прогон.
