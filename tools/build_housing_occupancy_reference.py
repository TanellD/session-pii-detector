"""Среднее число жителей занятого жилого помещения по типу, ВПН-2020, таблица 11.6."""

import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook
from build_regional_statistics_registry import normalized


LABELS = {"индивидуальные дома": "individual_house", "отдельные квартиры": "apartment",
          "коммунальные квартиры": "communal_apartment"}
SECTIONS = {"Городские и сельские населенные пункты": "all", "Городские населенные пункты": "urban",
            "Сельские населенные пункты": "rural"}


def extract_counts(row):
    occupied = int(row[1])
    one = sum(size * int(row[2 + size]) for size in range(1, 5)) + int(row[8])
    people = one + sum(int(row[index]) for index in (10, 12, 14))
    if occupied != sum(int(row[index]) for index in (2, 9, 11, 13)):
        raise ValueError("Группы числа домохозяйств не сходятся с числом помещений")
    if row[2] != sum(int(row[index]) for index in (3, 4, 5, 6, 7)):
        raise ValueError("Группы размера домохозяйства не сходятся с итогом")
    if occupied <= 0 or people < occupied:
        raise ValueError("Некорректная численность")
    return {"occupied_units": occupied, "residents": people, "mean_residents_per_occupied_unit": people / occupied}


def build():
    path = Path("data/external/annotation-statistics/housing-table6.xlsx")
    snapshot = json.loads(Path("data/external/geography/territorial-regions-2026-11-15.json").read_text(encoding="utf-8"))
    names = {normalized(r["region"]): r["region"] for r in snapshot["regions"]}
    names[normalized("Российская Федерация")] = "Российская Федерация"
    workbook = load_workbook(path, read_only=True, data_only=True)
    output_rows = []
    region = section = None
    try:
        for sheet in workbook:
            for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                label = " ".join(str(row[0] or "").split())
                if normalized(label) in names:
                    region = names[normalized(label)]
                    section = None
                elif label in SECTIONS:
                    section = SECTIONS[label]
                elif region and section and label in LABELS:
                    if not all(isinstance(value, (int, float)) for value in row[1:15]) or row[1] == 0:
                        continue
                    result = extract_counts(row)
                    output_rows.append({"region": region, "settlement": section, "housing_type": LABELS[label],
                                        **result, "sheet": sheet.title, "excel_row": index})
                elif label and all(value is None for value in row[1:15]) and not label.startswith(("из них", "в том числе")):
                    if label not in SECTIONS:
                        region = section = None
    finally:
        workbook.close()
    output = Path("data/reference/housing-occupancy-vpn2020.json")
    reference = {"source_url": "https://rosstat.gov.ru/storage/mediabank/Tom11_tab6_VPN-2020.xlsx",
                 "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reference": "ВПН-2020",
                 "rows": output_rows, "status": "approved_temporary_type_specific_approximation",
                 "limitations": ["Среднее по занятым помещениям, не ожидаемый размер помещения случайного жителя.",
                                 "Квартира не равна всему многоквартирному дому.",
                                 "Пустующие помещения не входят в знаменатель."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reference": str(output), "rows": len(output_rows), "national": [r for r in output_rows
                     if r["region"] == "Российская Федерация"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
