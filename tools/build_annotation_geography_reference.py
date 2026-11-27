"""Индекс переписных территорий с контекстом региона; без подстановки отсутствующих деревень."""

import hashlib
import json
from pathlib import Path
import re

from openpyxl import load_workbook
from build_regional_statistics_registry import normalized


def population_band(value):
    if value is None:
        return None
    if value < 0:
        raise ValueError("Численность не может быть отрицательной")
    bands = [(1000, "under_1000"), (10000, "1000_to_10000"), (100000, "10000_to_100000"),
             (500000, "100000_to_500000"), (1000000, "500000_to_1000000")]
    return next((name for bound, name in bands if value < bound), "at_least_1000000")


def build():
    path = Path("data/external/geography/rosstat-vpn2020-table5.xlsx")
    snapshot = json.loads(Path("data/external/geography/territorial-regions-2026-11-15.json").read_text(encoding="utf-8"))
    names = {normalized(r["region"]): r["region"] for r in snapshot["regions"]}
    names[normalized("Российская Федерация")] = "Российская Федерация"
    workbook = load_workbook(path, read_only=True, data_only=True)
    rows = []
    region = context = None
    settlement_pattern = r"^(?:г\.|город |пгт |рп |р\.п\.|с\.|село |д\.|деревня |поселок |посёлок |п\.|станица |ст-ца )"
    try:
        for sheet in workbook:
            for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                label = " ".join(str(row[0] or "").split())
                if len(row) < 4 or not isinstance(row[1], (int, float)):
                    continue
                key = normalized(label)
                if "федеральный округ" in label.lower():
                    region = context = None
                    continue
                if key in names:
                    region = names[key]
                    context = None
                    kind = "country" if region == "Российская Федерация" else "region"
                else:
                    if not region or label.lower().startswith(("городское население", "сельское население", "прочие", "всего")):
                        # Объединённое название может всё же содержать конкретный город.
                        if not region or not re.search(r" - (?:г\.|город )", label):
                            continue
                    tail = label.split(" - ")[-1]
                    if re.match(settlement_pattern, tail, re.IGNORECASE):
                        label = tail
                        kind = "settlement"
                    else:
                        if label.endswith(("область", "край", "республика")) or "область (" in label.lower():
                            # Нераспознанный заголовок субъекта не наследует предыдущий субъект.
                            region = context = None
                            continue
                        kind = "territory"
                        context = label
                if not region:
                    continue
                values = {sex: int(row[col]) if isinstance(row[col], (int, float)) else None
                          for sex, col in (("all", 1), ("male", 2), ("female", 3))}
                for sex, other in (("male", "female"), ("female", "male")):
                    if values[sex] is None and (values["all"] == 0 or values[other] == values["all"]):
                        values[sex] = 0
                if values["male"] is not None and values["female"] is not None and values["all"] != values["male"] + values["female"]:
                    raise ValueError(f"Не согласованы счётчики: {sheet.title}/{index}")
                rows.append({"id": f"{sheet.title}:{index}", "label": label, "region": region,
                             "context": context, "kind": kind, **values, "population_band": population_band(values["all"])})
    finally:
        workbook.close()
    result = {"source_url": "https://rosstat.gov.ru/storage/mediabank/Tom1_tab-5_VPN-2020.xlsx",
              "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reference": "ВПН-2020",
              "limitations": "Иерархические строки пересекаются. Сельские населённые пункты опубликованы от 3000 жителей. "
                             "Название без выбранной строки не разрешает географическую неоднозначность.", "rows": rows}
    output = Path("data/reference/annotation-geography-vpn2020.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reference": str(output), "indexed_rows": len(rows),
                      "settlement_rows": sum(r["kind"] == "settlement" for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    build()
