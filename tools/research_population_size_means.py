"""Воспроизводимые средние ВПН-2020/2021; предложения, не утверждённые коэффициенты."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
from statistics import median
from urllib.request import Request, urlopen
import zipfile

VERSION = "data_allsettlements_anon_156_v20260925_parquet.zip"
URL = "https://storage.yandexcloud.net/tochno-st-catalog/Rosstat/data_settlements_156_v20260925/" + VERSION
CACHE = Path("data/external/annotation-size-statistics")
HOUSING_VERSION = "data_gisgkh_157_v20260917_parquet.zip"
HOUSING_URL = "https://storage.yandexcloud.net/tochno-st-catalog/Minstroy/data_gisgkh_157_v20260917/" + HOUSING_VERSION


def download_archive(url, path):
    if path.exists():
        with zipfile.ZipFile(path):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    start = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "session-pii research"}
    if start:
        headers["Range"] = f"bytes={start}-"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        if start and (response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-")):
            raise ValueError("Сервер не подтвердил диапазон докачивания")
        length = int(response.headers["Content-Length"])
        expected = start + length
        if expected > 200000000:
            raise ValueError("Архив превышает лимит размера")
        with partial.open("ab" if start else "wb") as out:
            while chunk := response.read(1048576):
                out.write(chunk)
    if partial.stat().st_size != expected:
        raise ValueError("Неполная загрузка; повторите команду для докачивания")
    with zipfile.ZipFile(partial):
        pass
    partial.replace(path)


def statistics(values):
    total = sum(values)
    return {"objects": len(values), "inhabited_objects": sum(v > 0 for v in values),
            "residents": total, "object_mean": total / len(values),
            "object_median": median(values),
            "inhabited_object_median": median([v for v in values if v > 0]) if total else None,
            "resident_weighted_mean": sum(v*v for v in values) / total if total else None}


def object_type(name, dadata_type):
    # Переписное название приоритетнее современного геокодера.
    patterns = [(r"^(г\.|город)\s", "город"), (r"^(пгт|рп|рабочий пос[её]лок)\s", "ПГТ/рабочий посёлок"),
                (r"^(деревня|дер\.|д\.)\s*", "деревня"), (r"^(село|с\.)\s", "село"),
                (r"^(пос[её]лок|п\.)\s", "посёлок"), (r"^станица\s", "станица"),
                (r"^хутор\s", "хутор"), (r"^аул\s", "аул")]
    for pattern, kind in patterns:
        if re.match(pattern, name, re.I):
            return kind
    return "прочее: " + (dadata_type or "тип не установлен")


def build(rows):
    federal = {r["region"] for r in rows if r["object_level"] == "Город федерального значения"}
    region_totals = {r["region"]: r["population"] for r in rows
                     if r["object_level"] in {"Регион", "Город федерального значения"}}
    country = next(r["population"] for r in rows if r["object_level"] == "Страна")
    selected, excluded, ids = [], [], set()
    for r in rows:
        if r["object_level"] == "Город федерального значения":
            selected.append({**r, "kind": "город"})
        elif r["object_level"] == "Населенный пункт":
            if r["region"] in federal:
                continue  # Внутренние пункты уже входят в федеральный город.
            if r["oktmo"] == "11701363001":
                # Фактория — внутригородской округ, ошибочно помеченный как пункт.
                # Сохраняем исключение для проверки, исходный архив не исправляем.
                excluded.append(r)
                continue
            selected.append({**r, "kind": object_type(r["object_name"], r["settlement_type_full_dadata"])})
    groups = defaultdict(list)
    totals = Counter()
    for r in selected:
        if r["oktmo"] in ids:
            raise ValueError("Повторный ОКТМО")
        ids.add(r["oktmo"])
        n = r["population"]
        if n is None or n < 0:
            raise ValueError("Неизвестное/отрицательное население")
        totals[r["region"]] += n
        groups[(r["region"], r["kind"])].append(n)
        groups[("Российская Федерация", r["kind"])].append(n)
    discrepancies = {k: totals[k]-v for k,v in region_totals.items() if totals[k] != v}
    if discrepancies or sum(totals.values()) != country:
        raise ValueError(f"Не сходятся итоги: {discrepancies}")
    return {"approved_for_annotation": False, "population_date": "2021-10-01",
            "source": "https://tochno.st/datasets/allsettlements", "download_url": URL,
            "audit": {"selected_objects": len(selected), "regions": len(totals),
                      "population": country, "region_discrepancies": discrepancies,
                      "excluded_source_rows": excluded,
                      "federal_city_policy": "whole city, exclude nested settlements",
                      "type_policy": "census prefix, otherwise Dadata as explicitly labelled other"},
            "groups": [{"region": region, "object_type": kind, **statistics(values)}
                       for (region,kind),values in sorted(groups.items())],
            "housing_proposal": {"source": "https://www.urbaneconomics.ru/sites/default/files/kompleksnyi_analiz_harakteristik_zhilishchnogo_fonda_rossiiskoi_federacii.pdf",
                "stock_year": 2024, "apartments_millions": 51.58, "buildings_millions": 1.5201,
                "apartments_per_building_object_mean": 51.58/1.5201,
                "occupied_apartment_residents_2021": 2.3152949159,
                "estimated_building_residents_object_mean": 51.58/1.5201*2.3152949159,
                "assumption": "all apartments occupied; combines 2024 stock and 2021 occupancy; not resident weighted"}}


def housing_statistics(rows):
    groups, ids = defaultdict(list), set()
    quality = Counter()
    for r in rows:
        quality["all_rows"] += 1
        if not r["gisgkh_guid"] or r["gisgkh_guid"] in ids:
            raise ValueError("Отсутствующий или повторный ID дома")
        ids.add(r["gisgkh_guid"])
        if r["house_type"] != "Многоквартирный":
            quality["not_apartment_building"] += 1
            continue
        if r["life_cycle_stage"] != "Эксплуатация":
            quality["not_confirmed_in_operation"] += 1
            continue
        if r["flats"] is None or r["flats"] <= 0:
            quality["unknown_or_nonpositive_flats"] += 1
            continue
        quality["included_buildings"] += 1
        groups[r["region_name"]].append(r["flats"])
        groups["Вся доступная выборка"].append(r["flats"])
    occupancy = 2.3152949159
    result = []
    for region, values in sorted(groups.items()):
        stats = statistics(values)
        result.append({"region": region, "buildings": len(values),
            "apartments_total": sum(values), "apartments_object_mean": stats["object_mean"],
            "apartments_object_median": stats["object_median"],
            "estimated_residents_object_mean": stats["object_mean"] * occupancy,
            "estimated_residents_object_median": stats["object_median"] * occupancy,
            "estimated_residents_resident_weighted_mean": stats["resident_weighted_mean"] * occupancy})
    return {"source": "https://tochno.st/datasets/gisgkh", "download_url": HOUSING_URL,
        "reference_period": "2026-08", "approved_for_annotation": False,
        "national_coverage_complete": False, "quality": dict(quality), "groups": result,
        "assumptions": ["All apartments occupied", "Same 2021 occupancy for every building",
            "Included records are not proven representative of missing buildings",
            "Apartment count is not an observed resident count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--housing", action="store_true", help="Дополнить проверкой ГИС ЖКХ; архив должен быть скачан")
    parser.add_argument("--output", type=Path, default=Path("data/reference/proposed-population-size-means.json"))
    args = parser.parse_args()
    path = CACHE / VERSION
    if args.download:
        download_archive(URL, path)
        if args.housing:
            download_archive(HOUSING_URL, CACHE / HOUSING_VERSION)
    import pyarrow.parquet as pq
    with zipfile.ZipFile(path) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".parquet") and not n.startswith("__"))
        rows = pq.read_table(io.BytesIO(archive.read(member)), columns=["object_level", "object_name",
            "oktmo", "region", "population", "settlement_type_full_dadata"]).to_pylist()
    result = build(rows)
    if args.housing:
        housing_path = CACHE / HOUSING_VERSION
        with zipfile.ZipFile(housing_path) as archive:
            member = next(n for n in archive.namelist() if n.endswith(".parquet") and not n.startswith("__"))
            housing_rows = pq.read_table(io.BytesIO(archive.read(member)), columns=["flats",
                "region_name", "house_type", "life_cycle_stage", "gisgkh_guid"]).to_pylist()
        result["housing_registry"] = housing_statistics(housing_rows)
        result["housing_registry"]["archive_sha256"] = hashlib.sha256(housing_path.read_bytes()).hexdigest()
    result["archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"audit": result["audit"], "national_groups": [r for r in result["groups"]
          if r["region"] == "Российская Федерация" and not r["object_type"].startswith("прочее")],
          "housing": result["housing_proposal"],
          "registry_quality": result.get("housing_registry", {}).get("quality"),
          "registry_means": [r for r in result.get("housing_registry", {}).get("groups", [])
              if r["region"] == "Вся доступная выборка"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
