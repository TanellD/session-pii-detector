"""Загрузить агрегаты Росстата и показать доли для точных названий строк."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from openpyxl import load_workbook


SOURCE_URL = "https://rosstat.gov.ru/storage/mediabank/Tom1_tab-5_VPN-2020.xlsx"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/external/geography/rosstat-vpn2020-table5.xlsx"))
    parser.add_argument("--download", action="store_true", help="Загрузить файл, если локальной копии нет")
    parser.add_argument("--name", action="append", help="Точное название строки; можно повторить")
    args = parser.parse_args()
    if args.download and not args.input.exists():
        with urlopen(Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
            raw = response.read(25 * 1024 * 1024 + 1)
        if len(raw) > 25 * 1024 * 1024 or not raw.startswith(b"PK"):
            parser.error("Ответ не является XLSX допустимого размера")
        args.input.parent.mkdir(parents=True, exist_ok=True)
        args.input.write_bytes(raw)
    if not args.input.exists():
        parser.error("Нет локального файла; используйте --download")
    names = set(args.name or ["Российская Федерация", "Белгородская область", "г. Валуйки"])
    workbook = load_workbook(args.input, read_only=True, data_only=True)
    matches = []
    country_population = None
    try:
        for sheet in workbook:
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if len(row) < 4:
                    continue
                label = str(row[0] or "").strip()
                if label == "Российская Федерация" and isinstance(row[1], (int, float)):
                    country_population = row[1]
                if label in names and isinstance(row[1], (int, float)):
                    matches.append({"sheet": sheet.title, "excel_row": row_number, "name": label,
                                    "population": row[1], "male": row[2], "female": row[3]})
    finally:
        workbook.close()
    if not country_population:
        parser.error("Не найдена численность населения РФ")
    for match in matches:
        match["share_of_russia"] = match["population"] / country_population
    print(json.dumps({"source_url": SOURCE_URL, "snapshot": "ВПН-2020, таблица 5",
                      "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                      "russia_population": country_population, "rows": matches,
                      "missing_names": sorted(names - {row["name"] for row in matches}),
                      "limitations": "Точное совпадение строки не разрешает географическую неоднозначность. "
                                      "Строки разных уровней вложены: суммировать их нельзя. "
                                      "Не является оценкой текущего населения."}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
