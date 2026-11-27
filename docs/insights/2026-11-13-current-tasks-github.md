# Актуальные задачи MVP нужно сверять с GitHub

- Дата: 2026-11-13
- Автор: команда проекта
- Связано: issues #39, #42, #43; PR #19, #20

## Что увидели

Локальные страницы базы знаний содержат прежние статусы исследования. На GitHub уже слиты изменения по метрикам и обзору детекторов, а за TanellD закреплены рабочие задачи: согласование минимальных правил первого алерта, сборка минимального сессионного сервиса и воспроизводимая демонстрация.

## Доказательство

- https://github.com/TanellD/session-pii-detector/issues/39
- https://github.com/TanellD/session-pii-detector/issues/42
- https://github.com/TanellD/session-pii-detector/issues/43
- https://github.com/TanellD/session-pii-detector/pull/19
- https://github.com/TanellD/session-pii-detector/pull/20

Проверка актуальных назначений:

```powershell
gh issue list --repo TanellD/session-pii-detector --assignee TanellD --state open --json number,title,url
gh pr list --repo TanellD/session-pii-detector --state merged --json number,title,mergedAt,url
```

## Что это значит для проекта

Напоминание о текущей личной задаче должно опираться на назначения и описания issues. Старые локальные статусы не подтверждают текущее состояние main. Подготовка каркаса сервиса возможна до готовности зависимостей, завершение требует подключения реальных компонентов и принятой версии правил.
