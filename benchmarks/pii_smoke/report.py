"""Render only verified facts from a run; manual interpretation lives separately."""

import argparse
import json
from pathlib import Path

from .evaluate import aggregate, counts, latency_summary


def load_verified(directory):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if not summary["modes"]:
        raise ValueError("No measured modes")
    rows_by_mode = {}
    canonical = None
    for mode, metrics in summary["modes"].items():
        if mode not in {"regex", "gliner", "combined"}:
            raise ValueError(f"Unknown mode: {mode}")
        rows = json.loads((directory / f"{mode}.json").read_text(encoding="utf-8"))
        if len(rows) != summary["case_count"] or not rows:
            raise ValueError(f"Case count mismatch: {mode}")
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate case IDs: {mode}")
        for row in rows:
            if len(row["latency_ms"]) != summary["metadata"]["config"]["repeats"]:
                raise ValueError(f"Repeat count mismatch: {mode}/{row['id']}")
            for entity in row["expected"] + row["predicted"]:
                if not 0 <= entity["start"] < entity["end"] <= len(row["text"]):
                    raise ValueError(f"Invalid span: {mode}/{row['id']}")
        fixture = [
            {key: row[key] for key in ("id", "text", "expected")} for row in rows
        ]
        if canonical is not None and fixture != canonical:
            raise ValueError("Modes contain different fixtures")
        canonical = fixture
        if (
            aggregate(rows) != metrics["exact"]
            or aggregate(rows, True) != metrics["overlap"]
            or latency_summary(rows) != metrics["latency_ms"]
        ):
            raise ValueError(
                f"Stored metrics disagree with raw predictions/timings: {mode}"
            )
        rows_by_mode[mode] = rows
    return summary, rows_by_mode


def render(summary, rows_by_mode):
    meta = summary["metadata"]
    lines = [
        "# Автоматический отчёт CPU smoke-проверки #12",
        "",
        f"Дата запуска (UTC): `{meta.get('started_at_utc', 'не записана в старом формате')}`.",
        "Автор: `Kopylov-Andrey`. Измерение извлечения сущностей; HTTP-сервис не запускался.",
        "",
        "## Протокол",
        "",
        f"- Коммит: `{meta['commit']}`; dirty: `{meta['worktree_dirty']}`.",
        f"- Набор: `{meta.get('dataset_version', 'не записан в старом формате')}`; фраз: {summary['case_count']}.",
        f"- Dataset SHA-256: `{meta['dataset_sha256']}`.",
        f"- Config SHA-256: `{meta['config_sha256']}`.",
        f"- Python: `{meta['python']}`; платформа: `{meta['platform']}`.",
        f"- CPU: `{meta['processor'] or 'модель не записана'}`; логических потоков: {meta['logical_cpus']}.",
        f"- RAM: {meta['ram_gib']:.2f} GiB; свободно в начале: {meta['available_ram_gib_at_start']:.2f} GiB.",
        f"- Device: `{meta['device']}`; повторы: {meta['config']['repeats']}; batch=1.",
        "- Пакеты: "
        + ", ".join(f"`{key}=={value}`" for key, value in meta["packages"].items())
        + ".",
    ]
    if "model_load_seconds" in meta:
        lines += [
            f"- Модель: `{meta['config']['model']}` @ `{meta['config']['revision']}`.",
            f"- Backbone: `{meta['config']['backbone']}` @ `{meta['config']['backbone_revision']}`.",
            f"- Torch threads: {meta['config']['threads']}; threshold: {meta['config']['threshold']}.",
            f"- Загрузка: {meta['model_load_seconds']:.3f} s; прогрев: {meta['warmup_seconds']:.3f} s, вне латенси.",
        ]
    if "environment_lock_sha256" in meta:
        lines.append(f"- Lock SHA-256: `{meta['environment_lock_sha256']}`.")
    lines += [
        "",
        "## Результаты",
        "",
        "Числа пересчитаны из сырых predictions и latency_ms; p95 — nearest-rank по всем повторам.",
        "",
        "| Режим | TP / FP / FN, точные границы | TP / FP / FN, пересечение | Median, ms | p95, ms | Измерений |",
        "|---|---|---|---|---|---|",
    ]
    for mode, metrics in summary["modes"].items():

        def triple(value):
            return f"{value['tp']} / {value['fp']} / {value['fn']}"

        timing = metrics["latency_ms"]
        lines.append(
            f"| {mode} | {triple(metrics['exact']['total'])} | {triple(metrics['overlap']['total'])} | "
            f"{timing['median']:.3f} | {timing['p95']:.3f} | {timing['samples']} |"
        )
    selected = "combined" if "combined" in rows_by_mode else next(iter(rows_by_mode))
    lines += [
        "",
        f"## По типам: {selected}, точные границы",
        "",
        "| Тип | TP | FP | FN |",
        "|---|---|---|---|",
    ]
    for kind, c in summary["modes"][selected]["exact"]["by_type"].items():
        lines.append(f"| {kind} | {c['tp']} | {c['fp']} | {c['fn']} |")
    lines += [
        "",
        f"## Проверенные случаи: {selected}",
        "",
        "| ID | TP | FP | FN |",
        "|---|---|---|---|",
    ]
    for row in rows_by_mode[selected]:
        c = counts(row["expected"], row["predicted"])
        lines.append(f"| {row['id']} | {c['tp']} | {c['fp']} | {c['fn']} |")
    errors = [
        row
        for row in rows_by_mode[selected]
        if any(counts(row["expected"], row["predicted"])[key] for key in ("fp", "fn"))
    ]
    lines += ["", "## Расхождения на синтетических фразах", ""]
    if not errors:
        lines.append("Расхождений по точному типу и границам в этом прогоне нет.")
    for row in errors:

        def spans(entities):
            return (
                "; ".join(
                    f"{e['type']}: {row['text'][e['start'] : e['end']]}"
                    for e in entities
                )
                or "нет"
            )

        lines += [
            f"- `{row['id']}`: {row['text']}",
            f"  Gold: {spans(row['expected'])}. Predictions: {spans(row['predicted'])}.",
        ]
    lines += [
        "",
        "## Границы измерения",
        "",
        "Это не сессионный F2. PERSON объединяет ФИО и части; классификация частей, линковка людей и истинность фактов не оценивались.",
        "Набор мал и синтетический, частоты не репрезентативны. Повторы не являются независимыми фразами; любое пересечение — мягкая диагностика границ.",
        "Латенси коротких текстов не включает HTTP, сеть, нагрузку и хранение состояния. Протокол combined указан в README и metadata; старый v1 исключал конверсию адаптера.",
        "Ручные выводы относятся к указанному run и оформляются отдельно. Изменение числа ошибок само по себе не устанавливает их причину.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("runs/task12-smoke-v2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.input / "report.md"
    summary, rows = load_verified(args.input)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(summary, rows), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
