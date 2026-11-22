"""Свести выгрузку ФНС к распределению численности без сохранения записей организаций и ФИО."""

import argparse
from collections import Counter, defaultdict
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile


DEFAULT_URL = "https://file.nalog.ru/opendata/7707329152-sshr2019/data-20261025-structure-20200408.zip"


def quantile(histogram, position):
    cumulative = 0
    for value, frequency in sorted(histogram.items()):
        cumulative += frequency
        if cumulative > position:
            return value
    raise ValueError("Пустое распределение")


def describe(histogram):
    n = sum(histogram.values())
    if not n:
        return None
    total = sum(size * count for size, count in histogram.items())
    return {"reporting_organizations": n, "sum_reported_average_headcount": total,
            "mean_by_organization": total / n,
            "median_by_organization": (quantile(histogram, (n - 1) // 2) + quantile(histogram, n // 2)) / 2,
            "size_weighted_mean": sum(size * size * count for size, count in histogram.items()) / total if total else None,
            "histogram": {str(k): v for k, v in sorted(histogram.items())}}


def summarize(raw):
    histograms = defaultdict(Counter)
    seen = set()
    duplicate_records = missing_records = 0
    with ZipFile(BytesIO(raw)) as archive:
        members = [item for item in archive.infolist() if item.filename.lower().endswith(".xml")]
        if sum(item.file_size for item in members) > 1024 * 1024 * 1024:
            raise ValueError("Превышен лимит распакованного XML")
        for member in members:
            document = None
            with archive.open(member) as stream:
                for event, element in iterparse(stream, events=("start", "end")):
                    tag = element.tag.rsplit("}", 1)[-1]
                    if event == "start" and tag == "Документ":
                        document = {"date": element.get("ДатаСост"), "key": None, "size": None}
                    elif event == "end":
                        if document is not None:
                            if tag == "СведНП" and element.get("ИННЮЛ"):
                                document["key"] = hashlib.sha256(element.get("ИННЮЛ").encode()).digest()
                            elif tag == "СведССЧР":
                                value = element.get("КолРаб")
                                if value is not None:
                                    document["size"] = int(value)
                            elif tag == "Документ":
                                if not document["date"] or document["key"] is None or document["size"] is None:
                                    missing_records += 1
                                elif document["size"] < 0:
                                    raise ValueError("Отрицательная численность")
                                else:
                                    key = (document["key"], document["date"])
                                    if key in seen:
                                        duplicate_records += 1
                                    else:
                                        seen.add(key)
                                        histograms[document["date"]][document["size"]] += 1
                                document = None
                        element.clear()
    return {"groups_by_reporting_date": {
                date: {"including_zero": describe(hist), "positive_only": describe(Counter({k: v for k, v in hist.items() if k > 0}))}
                for date, hist in sorted(histograms.items())},
            "duplicate_records_skipped": duplicate_records, "incomplete_records_skipped": missing_records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=Path("data/reference/organization-size-fns-20261025.json"))
    parser.add_argument("--show", action="store_true", help="Показать ранее рассчитанную сводку без загрузки")
    args = parser.parse_args()
    if args.show:
        result = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        with urlopen(Request(args.url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
            raw = response.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            parser.error("Архив превышает лимит размера")
        result = summarize(raw)
        result.update({"source_url": args.url, "sha256": hashlib.sha256(raw).hexdigest(),
                       "scope": "Юрлица с опубликованной среднесписочной численностью ФНС; не все работодатели/ИП и не текущая численность.",
                       "limitations": "Среднесписочная численность не тождественна списку уникальных работающих людей. "
                                       "Отсутствующие сведения не считаются нулём. Размер конкретного работодателя не устанавливается."})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public = {key: value for key, value in result.items() if key != "groups_by_reporting_date"}
    public["groups_by_reporting_date"] = {date: {scope: {k: v for k, v in stats.items() if k != "histogram"} if stats else None
                                                       for scope, stats in group.items()}
                                                 for date, group in result["groups_by_reporting_date"].items()}
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
