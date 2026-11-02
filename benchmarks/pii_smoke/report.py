"""Render measured aggregates and synthetic error cases, without copying raw data."""

import argparse
import json
from pathlib import Path

from .evaluate import aggregate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("runs/task12-smoke"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/experiments/2026-11-02-task12-gliner-cpu.md"),
    )
    args = parser.parse_args()
    summary = json.loads((args.input / "summary.json").read_text(encoding="utf-8"))
    meta = summary["metadata"]
    for mode, metrics in summary["modes"].items():
        rows = json.loads((args.input / f"{mode}.json").read_text(encoding="utf-8"))
        if (
            aggregate(rows) != metrics["exact"]
            or aggregate(rows, True) != metrics["overlap"]
        ):
            raise ValueError(f"Stored counts disagree with raw predictions: {mode}")
    lines = [
        "# Эксперимент #12: regex + GLiNER на русском, CPU",
        "",
        "Дата: 02.11.2026. Автор: `Kopylov-Andrey`. Issue: #12. Статус: выполнен локально.",
        "",
        "Гипотеза: форматные правила дополняют zero-shot NER; новые семантические классы требуют проверки.",
        "",
        "## Воспроизведение",
        "",
        "[Команды и ограничения](../../benchmarks/pii_smoke/README.md). Из корня репо:",
        "",
        "```powershell",
        ".\\.venv-research\\Scripts\\python.exe -m benchmarks.pii_smoke.dataset",
        ".\\.venv-research\\Scripts\\python.exe -m benchmarks.pii_smoke.run",
        ".\\.venv-research\\Scripts\\python.exe -m benchmarks.pii_smoke.report",
        "```",
        "",
        "Перед run подготовить кеш и включить offline, как в README.",
        "",
        f"- Коммит запуска: `{meta['commit']}`; dirty: `{meta['worktree_dirty']}`.",
        f"- Данные: `task12-smoke-v1`; SHA-256: `{meta['dataset_sha256']}`.",
        f"- Конфиг SHA-256 (байты исходного файла): `{meta['config_sha256']}`.",
        f"- Модель: `{meta['config']['model']}` @ `{meta['config']['revision']}`.",
        f"- Backbone: `{meta['config']['backbone']}` @ `{meta['config']['backbone_revision']}`.",
        f"- Python: `{meta['python']}`; платформа: `{meta['platform']}`.",
        f"- CPU: {meta['logical_cpus']} логических потоков; точная модель CPU не записана платформой.",
        f"- RAM: {meta['ram_gib']:.2f} GiB; свободно в начале {meta['available_ram_gib_at_start']:.2f} GiB.",
        f"- Device: CPU, torch threads={meta['config']['threads']}; batch=1; порог={meta['config']['threshold']}.",
        "- Пакеты: "
        + ", ".join(f"`{k}=={v}`" for k, v in meta["packages"].items())
        + ".",
        f"- Загрузка: {meta['model_load_seconds']:.3f} s; прогрев: {meta['warmup_seconds']:.3f} s (отдельно от латенси).",
        "",
        "## Результаты",
        "",
        "Все числа ниже получены report из summary и повторно сверены с сырыми предсказаниями.",
        "",
        "| Режим | TP / FP / FN, точные границы | TP / FP / FN, пересечение | CPU median, ms | CPU p95, ms |",
        "|---|---|---|---|---|",
    ]
    for mode, metrics in summary["modes"].items():
        exact, overlap = metrics["exact"]["total"], metrics["overlap"]["total"]

        def triple(c):
            return f"{c['tp']} / {c['fp']} / {c['fn']}"

        lines.append(
            f"| {mode} | {triple(exact)} | {triple(overlap)} | {metrics['latency_ms']['median']:.3f} | {metrics['latency_ms']['p95']:.3f} |"
        )
    lines += [
        "",
        f"Фразы: {summary['case_count']}. Измерения на режим: {summary['modes']['combined']['latency_ms']['samples']} повторов, а не столько независимых фраз.",
        "",
        "### По типам, combined, точные границы",
        "",
        "| Тип | TP | FP | FN |",
        "|---|---|---|---|",
    ]
    for kind, c in summary["modes"]["combined"]["exact"]["by_type"].items():
        lines.append(f"| {kind} | {c['tp']} | {c['fp']} | {c['fn']} |")
    lines += [
        "",
        "## Анализ ошибок",
        "",
        "Синтетические примеры; строки воспроизводятся тем же report.",
        "",
    ]
    rows = json.loads((args.input / "combined.json").read_text(encoding="utf-8"))
    for row in rows:
        if row["id"] in {
            "first_name",
            "occupation",
            "relationship",
            "inn",
            "intimacy",
            "organization",
        }:
            gold = "; ".join(
                f"{e['type']}: {row['text'][e['start'] : e['end']]}"
                for e in row["expected"]
            )
            pred = (
                "; ".join(
                    f"{e['type']}: {row['text'][e['start'] : e['end']]}"
                    for e in row["predicted"]
                )
                or "ничего"
            )
            lines += [
                f"- `{row['id']}` — {row['text']}",
                f"  Ожидалось: {gold}. Получено: {pred}.",
            ]
    lines += [
        "",
        "## Вывод и следующий шаг",
        "",
        "В этом наборе правила улучшают покрытие форматов. Базовый GLiNER не готов как универсальный русский детектор: должность и родство не извлечены нужными типами, есть смешение PHONE/INN и ложные PERSON. Нужны адаптер, правила разрешения конфликтов и сравнение с другими весами на независимом наборе.",
        "",
        "Это не оценка сессионного F2. Gold, метки и порог не менялись после просмотра результата. Набор мал, с искусственными именами и номерами; выводы не переносятся на реальные частоты или production. Любое пересечение — мягкий диагностический критерий, не достаточное качество границ.",
        "",
        "Латенси относится к коротким текстам, не включает сеть/API и конверсию объектов адаптером. Не проверены параллельные сессии, длинные сообщения, рост состояния и пригодность порога. ~200 ms из ответа ментора относятся к другому железу и другому сервису.",
        "",
        "При загрузке были предупреждения Transformers о byte fallback / regex токенизатора и отсутствии max_length. Протокол не изменялся; отдельно проверить fast/slow tokenizer и длинные тексты на следующем этапе. Предупреждение само по себе не доказывает причину ошибок.",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
