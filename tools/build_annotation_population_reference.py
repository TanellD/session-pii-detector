"""Построить справочник населения и совместных возрастно-половых групп для разметки."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from openpyxl import load_workbook

from build_regional_statistics_registry import normalized


SECTIONS = {"Городское и сельское население": "all", "Городское население": "urban", "Сельское население": "rural"}
SEX_COLUMNS = {"all": 1, "male": 2, "female": 3}


def parse_groups(path, region_names):
    workbook = load_workbook(path, read_only=True, data_only=True)
    result = []
    region = section = None
    try:
        for sheet in workbook:
            for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                label = " ".join(str(row[0] or "").split())
                if normalized(label) in region_names and all(value is None for value in row[1:4]):
                    region = region_names[normalized(label)]
                    section = None
                    continue
                if "федеральный округ" in label.lower():
                    region = section = None
                    continue
                if label and all(value is None for value in row[1:4]) and not label.lower().startswith(("в том числе", "из общей")):
                    region = section = None
                    continue
                if not region:
                    continue
                band = None
                if label in SECTIONS:
                    section = SECTIONS[label]
                    band = "all"
                else:
                    match = re.fullmatch(r"(\d+)\s*[-–−]\s*(\d+)", label)
                    older = re.fullmatch(r"(\d+)\s+и более", label)
                    if match:
                        band = f"{match[1]}-{match[2]}"
                    elif older:
                        band = f"{older[1]}+"
                if band and section:
                    for sex, column in SEX_COLUMNS.items():
                        value = row[column]
                        if value is None or value == "-":
                            continue
                        if not isinstance(value, (int, float)) or value < 0 or int(value) != value:
                            raise ValueError(f"Некорректный счётчик: {sheet.title}/{index}/{column + 1}")
                        result.append({"region": region, "settlement": section, "age_band": band,
                                       "sex": sex, "count": int(value), "sheet": sheet.title,
                                       "excel_row": index, "excel_column": column + 1})
    finally:
        workbook.close()
    return result


def build(args):
    snapshot = json.loads(args.regions.read_text(encoding="utf-8"))
    names = {normalized(r["region"]): r["region"] for r in snapshot["regions"]}
    names[normalized("Российская Федерация")] = "Российская Федерация"
    rows = parse_groups(args.input, names)
    by_key = {}
    for row in rows:
        key = (row["region"], row["settlement"], row["age_band"], row["sex"])
        if key in by_key:
            raise ValueError(f"Повтор ключа: {key}")
        by_key[key] = row["count"]
    total = by_key[("Российская Федерация", "all", "all", "all")]
    diagnostics = []
    for row in rows:
        region_total = by_key.get((row["region"], "all", "all", "all"))
        section_total = by_key.get((row["region"], row["settlement"], "all", "all"))
        row["share_of_russia"] = row["count"] / total
        row["share_of_region"] = row["count"] / region_total if region_total else None
        row["share_of_settlement_section"] = row["count"] / section_total if section_total else None
        if section_total is not None and row["count"] > section_total:
            raise ValueError("Число группы больше населения её территории")
    for region, section in sorted({(r["region"], r["settlement"]) for r in rows}):
        subtotal = by_key[(region, section, "all", "all")]
        age_sum = sum(r["count"] for r in rows if r["region"] == region and r["settlement"] == section
                      and r["sex"] == "all" and r["age_band"] != "all")
        if age_sum > subtotal:
            raise ValueError("Возрастные группы превышают итог")
        if age_sum != subtotal:
            diagnostics.append({"region": region, "settlement": section, "age_not_covered": subtotal - age_sum})
    household_rows = []
    if args.households.exists():
        household_book = load_workbook(args.households, read_only=True, data_only=True)
        region = None
        try:
            for sheet in household_book:
                for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    label = " ".join(str(row[0] or "").split())
                    if not isinstance(row[1], (int, float)):
                        continue
                    if normalized(label) in names:
                        region = names[normalized(label)]
                        section = "all"
                    elif label in ("Городские населенные пункты", "Сельские населенные пункты") and region:
                        section = "urban" if label.startswith("Городские") else "rural"
                    else:
                        region = None
                        continue
                    household_rows.append({"region": region, "settlement": section,
                                           "households": row[1], "persons": row[2],
                                           "mean_household_size": row[2] / row[1] if row[1] else None,
                                           "size_bins": dict(zip(("1", "2", "3", "4", "5", "6+"), row[3:9])),
                                           "sheet": sheet.title, "excel_row": index,
                                           "interpretation": "Домохозяйство, не квартира/дом и не число людей по адресу"})
        finally:
            household_book.close()
    reference = {"reference_version": "vpn2020-age-sex-households-v1", "population": "Население России в срезе ВПН-2020",
                 "source_url": "https://rosstat.gov.ru/storage/mediabank/Tom2_tab2_VPN-2020.xlsx",
                 "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                 "russia_population": total, "rows": rows, "diagnostics": diagnostics,
                 "household_rows": household_rows,
                 "households_source": {"url": "https://rosstat.gov.ru/storage/mediabank/Tom8_tab2_VPN-2020.xlsx",
                                       "sha256": hashlib.sha256(args.households.read_bytes()).hexdigest()} if household_rows else None,
                 "not_covered": ["ФИО", "точная дата рождения", "должность", "контакты", "жильцы адреса"],
                 "limitations": "Счётчики переписного среза, не текущего населения. Возрастные группы не делятся равномерно. "
                                 "Доли вложенных территорий и группы не перемножаются повторно."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reference": str(args.output), "regions_including_russia": len({r["region"] for r in rows}),
                      "joint_rows": len(rows), "diagnostic_groups": len(diagnostics)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/external/geography/rosstat-vpn2020-age-sex.xlsx"))
    parser.add_argument("--regions", type=Path, default=Path("data/external/geography/territorial-regions-2026-11-15.json"))
    parser.add_argument("--output", type=Path, default=Path("data/reference/annotation-population-vpn2020.json"))
    parser.add_argument("--households", type=Path, default=Path("data/external/annotation-statistics/households-census.xlsx"))
    parser.add_argument("--query-household-region")
    parser.add_argument("--query-region")
    parser.add_argument("--age-band", default="all")
    parser.add_argument("--sex", choices=SEX_COLUMNS, default="all")
    parser.add_argument("--settlement", choices=SECTIONS.values(), default="all")
    args = parser.parse_args()
    if args.query_household_region:
        reference = json.loads(args.output.read_text(encoding="utf-8"))
        matches = [r for r in reference.get("household_rows", []) if normalized(r["region"]) == normalized(args.query_household_region)
                   and r["settlement"] == args.settlement]
        print(json.dumps({"matches": matches, "status": "found" if matches else "not_covered",
                          "source": reference.get("households_source")}, ensure_ascii=False, indent=2))
    elif args.query_region:
        reference = json.loads(args.output.read_text(encoding="utf-8"))
        matches = [r for r in reference["rows"] if normalized(r["region"]) == normalized(args.query_region)
                   and r["age_band"] == args.age_band and r["sex"] == args.sex and r["settlement"] == args.settlement]
        print(json.dumps({"matches": matches, "status": "found" if matches else "not_covered",
                          "source_url": reference["source_url"], "source_sha256": reference["source_sha256"]}, ensure_ascii=False, indent=2))
    else:
        build(args)


if __name__ == "__main__":
    main()
