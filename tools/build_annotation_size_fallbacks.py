"""Средние размеров территорий/жилья из полного согласованного входа; без выдуманных коэффициентов."""

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median


def build(payload):
    if payload.get("coverage_complete") is not True:
        raise ValueError("Нужен полный охват заявленной группы; усечённый список не годится")
    if not payload.get("source_url") or not payload.get("reference_date"):
        raise ValueError("Нужны источник и дата численности")
    if payload.get("unit") not in {"settlement", "street", "residential_building"}:
        raise ValueError("Нужно явно задать единицу: пункт, улица либо жилой дом")
    groups, ids = defaultdict(list), set()
    for row in payload["rows"]:
        if not row.get("id") or row["id"] in ids:
            raise ValueError("Повторный или отсутствующий ID объекта")
        ids.add(row["id"])
        n = row["population"]
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
            raise ValueError("Численность должна быть конечным неотрицательным числом")
        if not row.get("object_type") or not row.get("region"):
            raise ValueError("Нужны тип объекта и регион")
        groups[(row["region"], row["object_type"])].append(n)
        if payload.get("national_coverage_complete") is True:
            groups[("Российская Федерация", row["object_type"])].append(n)
    rows = []
    for (region, kind), counts in sorted(groups.items()):
        residents = sum(counts)
        rows.append({"region": region, "object_type": kind, "objects": len(counts),
                     "residents": residents, "object_mean": residents / len(counts),
                     "object_median": median(counts),
                     "resident_weighted_mean": sum(n * n for n in counts) / residents if residents else None})
    return {"source_url": payload["source_url"], "reference_date": payload["reference_date"],
            "unit": payload["unit"], "coverage_complete": True,
            "national_coverage_complete": payload.get("national_coverage_complete") is True,
            "source_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "method": "resident_weighted_mean", "approved_for_annotation": False,
            "limitation": "Source coverage must be checked before approval; mean is not a conservative upper bound", "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared_requires_source_review", "groups": len(result["rows"]),
                      "method": result["method"], "approved_for_annotation": False}))


if __name__ == "__main__":
    main()
