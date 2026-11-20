# Ветки и обсуждения после слияния протокола метрик

- Дата: 2026-11-11
- Автор: команда проекта
- Связано: PR #16/#19/#20, issue #13

## Что увидели

В `origin/main` принят PR #19, коммит `ba2652d`. Протокол задаёт ранний
первый алерт как только FP, поздний и отсутствующий — как FN. Старый комментарий
с предложением FP+FN не соответствует принятому тексту. Ответы Олеси от
10 ноября подтверждают границы задержки сервиса и необходимость учитывать
вычисление скора из атрибутов при подготовке разметки.

Обнаружена опубликованная ветка `research/13-russian-datasets-synthetic-plan`,
head `0929e1e`: предварительный аудит источников, сценарии, план синтетики,
промпты и предложения к разметке. PR для неё на момент проверки нет.
Наборы не допущены к использованию, пилот не выполнен. Сводка ветки устарела
в части статуса PR #19; описание #13 ещё утверждает отсутствие ветки.

GitHub показывает CONFLICTING у PR #16 и #20 относительно текущего main.
У #16 есть одобрение, у #20 требуется ревью. Старый зелёный CI не подтверждает
совместимость с обновлённой основной веткой. Их head-коммиты не изменились.
Ветка ответов ментора и презентации также остаётся отдельной, без PR.

## Доказательство

- [Принятый протокол](https://github.com/TanellD/session-pii-detector/blob/ba2652d6f7e7fb9f7eea001eaf14ab48d906069b/docs/kb/metrics.md).
- [Новая сводка данных](https://github.com/TanellD/session-pii-detector/blob/0929e1e/docs/project/data-research-summary.md).
- [Задача данных](https://github.com/TanellD/session-pii-detector/issues/13).
- [PR определения](https://github.com/TanellD/session-pii-detector/pull/16), [PR API](https://github.com/TanellD/session-pii-detector/pull/20).

Команды проверки из корня репозитория:

```powershell
git fetch --all
git log --all --date=iso-strict --format='%h %ad %d %s'
gh pr list --state all --limit 100 --json number,state,headRefName,headRefOid,updatedAt,mergedAt
foreach ($prId in @(16,19,20)) {
    gh pr view $prId --json state,headRefOid,mergeable,reviewDecision,comments,reviews,statusCheckRollup
}
gh api --paginate repos/TanellD/session-pii-detector/pulls/comments
gh api --paginate repos/TanellD/session-pii-detector/issues/comments
gh issue view 13 --json body,state,updatedAt
git show origin/research/13-russian-datasets-synthetic-plan:docs/project/data-research-summary.md
```

## Что это значит для проекта

Перед дальнейшими слияниями нужно согласовать ветки с main и сохранить
объединённый реестр источников. Политика телефона, роли анализа, смысл
текущего и исторического вердикта требуют отдельной сверки документов.
Обзор данных создаёт основу пилота, но не заменяет принятую политику,
машинную схему и инструкцию разметки. Новых комментариев после слияния
PR #19 в прочитанной истории не обнаружено.
