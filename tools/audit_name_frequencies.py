"""Аудит агрегированных частот частей ФИО; индивидуальные записи и примеры не выводятся."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request, urlopen

import pyarrow.parquet as parquet


def fetch(url, limit):
    with urlopen(Request(url, headers={"User-Agent": "SessionPiiResearch/0.1"}), timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Ответ превышает лимит")
    return raw


def normalize(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def audit(raw):
    metadata = parquet.ParquetFile(BytesIO(raw))
    table = metadata.read(columns=["text", "count", "gender"]).to_pydict()
    counts = []
    invalid = missing_labels = 0
    genders = Counter()
    text_keys = Counter()
    raw_keys = Counter()
    normalized_spellings = {}
    selected_signatures = Counter()
    normalized_count_mass = Counter()
    missing_label_count_mass = 0
    for label, count, gender in zip(table["text"], table["count"], table["gender"]):
        if not isinstance(label, str) or not label.strip():
            missing_labels += 1
        else:
            text_keys[normalize(label)] += 1
            raw_keys[label] += 1
            normalized_spellings.setdefault(normalize(label), set()).add(label)
        try:
            parsed = int(count)
            if isinstance(count, bool) or isinstance(count, float) and parsed != count or parsed < 0:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        counts.append(parsed)
        genders[str(gender)] += parsed
        selected_signatures[(label, parsed, str(gender))] += 1
        if isinstance(label, str) and label.strip():
            normalized_count_mass[normalize(label)] += parsed
        else:
            missing_label_count_mass += parsed
    collision_causes = Counter()
    for spellings in normalized_spellings.values():
        if len(spellings) < 2:
            continue
        if len({value.casefold() for value in spellings}) == 1:
            collision_causes["case_only"] += 1
        elif len({" ".join(value.casefold().split()) for value in spellings}) == 1:
            collision_causes["case_and_whitespace"] += 1
        else:
            collision_causes["includes_yo_e_merge"] += 1
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            "schema": str(metadata.schema_arrow), "rows": metadata.metadata.num_rows,
            "valid_counts": len(counts), "invalid_counts": invalid, "missing_labels": missing_labels,
            "sum_counts": sum(counts), "min_count": min(counts) if counts else None,
            "max_count": max(counts) if counts else None, "singleton_rows": sum(c == 1 for c in counts),
            "zero_count_rows": sum(c == 0 for c in counts),
            "normalized_duplicate_groups": sum(n > 1 for n in text_keys.values()),
            "normalization_audit": {
                "rows_in_normalized_collision_groups": sum(n for n in text_keys.values() if n > 1),
                "source_count_mass_in_normalized_collision_groups": sum(normalized_count_mass[k] for k, n in text_keys.items() if n > 1),
                "distinct_spelling_collision_groups": sum(len(values) > 1 for values in normalized_spellings.values()),
                "distinct_spelling_collision_causes": dict(collision_causes),
                "same_raw_spelling_multiple_rows_groups": sum(n > 1 for n in raw_keys.values()),
                "exact_text_count_gender_duplicate_groups": sum(n > 1 for n in selected_signatures.values()),
                "missing_label_source_count_mass": missing_label_count_mass,
                "scope": "Совпадения ключей/трёх выбранных полей; не доказанные дубли полных строк или людей. Счётчики не удаляются и не суммируются в новый словарь."},
            "count_sums_by_dictionary_gender": dict(genders),
            "interpretation": "Счётчики источника; словарная метка пола не подтверждает распределение пола носителей."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Анализировать в памяти; сохранить только обезличенную сводку")
    parser.add_argument("--revision", help="Зафиксированный commit SHA репозитория источника")
    parser.add_argument("--output", type=Path, default=Path("data/reference/name-frequency-audit.json"))
    args = parser.parse_args()
    if not args.download:
        print(args.output.read_text(encoding="utf-8"))
        return
    revision = None
    collections = {}
    if args.download:
        revision = args.revision or json.loads(fetch("https://api.github.com/repos/datacoon/russiannames/commits/master", 2 * 1024 * 1024))["sha"]
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            parser.error("Нужен полный commit SHA")
        for collection in ("names", "midnames", "surnames"):
            url = f"https://raw.githubusercontent.com/datacoon/russiannames/{revision}/data/parquet/{collection}.parquet"
            raw = fetch(url, 20 * 1024 * 1024)
            if not raw.startswith(b"PAR1"):
                raise ValueError("Ожидался агрегированный Parquet")
            collections[collection] = audit(raw)
            # Исходные ключи, записи и Parquet не сохраняются на диск.
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "repository": "https://github.com/datacoon/russiannames",
              "revision": revision, "collections": collections,
              "national_population_representativeness": "not_established",
              "joint_full_name_frequencies": "not_present_in_selected_collections"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
