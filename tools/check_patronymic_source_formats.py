"""Проверка структуры открытых источников: только схема и числовая сводка, без сохранения ФИО."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from urllib.request import Request, build_opener
import xml.etree.ElementTree as ET

from collect_vk_name_pairs import NoRedirect
from research_name_pairs import ROOT, write_private

CACHE = ROOT / "patronymic-source-formats-2026-11-17.json"
SOURCES = {
    "fns_rsmp_schema": "https://file.nalog.ru/opendata/7707329152-rsmp/structure-12052026.xsd",
    "cadastral_sro_csv": "https://kadastrsro.ru/index.php/reestr/?download=csv",
}


def fetch(url):
    request = Request(url, headers={"User-Agent": "SessionPiiResearch/0.1"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Response exceeds inspection bound")
    return raw


def inspect_schema(raw):
    root = ET.fromstring(raw)
    selected = []
    for element in root.iter():
        if element.attrib.get("name") in ("Фамилия", "Имя", "Отчество"):
            selected.append({"field": element.attrib["name"], "use": element.attrib.get("use")})
    return {"name_fields": selected, "patronymic_field_found": any(r["field"] == "Отчество" for r in selected)}


def inspect_csv(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t")
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader)
    fio_columns = [i for i, cell in enumerate(header) if "фио" in cell.lower() or
                   ("фамил" in cell.lower() and "отчеств" in cell.lower())]
    if not fio_columns:
        return {"recognized_fullname_column": False, "rows_counted": False}
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    return {"recognized_fullname_column": True, "column_count": len(header),
            "data_rows": len(rows), "rows_with_nonempty_fullname": sum(
                any(i < len(row) and row[i].strip() for i in fio_columns) for row in rows),
            "count_unit": "registry_rows_not_deduplicated_people", "personal_rows_saved": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if not args.download:
        print(CACHE.read_text(encoding="utf-8") if CACHE.exists() else json.dumps({"status": "no_cached_check"}))
        return
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "sources": {}}
    for key, url in SOURCES.items():
        try:
            raw = fetch(url)
            result = inspect_schema(raw) if key == "fns_rsmp_schema" else inspect_csv(raw)
            report["sources"][key] = {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), **result}
        except Exception as error:
            code = getattr(error, "code", None)
            report["sources"][key] = {"url": url, "status": "check_failed", "error_type": type(error).__name__,
                                      "http_code": code if type(code) is int else None}
    write_private(CACHE, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
