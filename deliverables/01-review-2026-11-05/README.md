# Первый чекпоинт — исследование и направление MVP

Задача, команда и текущие направления, результаты исследования,
предлагаемые границы MVP и план. Обновлено 06.11.2026.
Границы MVP и протокол метрик обозначены как предложения.

- [Презентация PDF](checkpoint.pdf).
- [Текст выступления по слайдам](speech.md).
- [Содержание и основания](presentation-outline.md).
- [Ответы ментора](../../docs/meetings/2026-11-05-mentor-answers.md).

## Воспроизведение

Из корня репозитория, Python 3.11 и Windows с системным Segoe UI:

```powershell
python -m pip install --target outputs/presentation-deps -r tools/requirements-presentation.txt
python tools/build_checkpoint_presentation.py --telegram '@tanellD'
Copy-Item -LiteralPath outputs/checkpoint-2026-11-05/session-pii-checkpoint.pdf -Destination deliverables/01-review-2026-11-05/checkpoint.pdf
```

При блокировке открытого PDF генератор выводит созданный путь с суффиксом
updated/revision; копировать нужно его. Также создаются PowerPoint,
PNG-превью и речь в игнорируемом outputs. Для новой версии синхронизировать
speech.md с текстом генератора.

Дата MVP взята из roadmap. Измеренных показателей качества сессионного
сервиса в презентации пока нет. Направления участников будут дополняться.
