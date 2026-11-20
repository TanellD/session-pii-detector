"""Ограниченный сбор проверенных открытых документов. Сохраняет только сводки.

python -X utf8 tools/batch_public_fullname_statistics.py --download
Без --download показывает сохранённый краткий отчёт. Индивидуальные записи,
исходный HTML и соответствия анонимных категорий никогда не записываются.
"""

from collections import defaultdict
from itertools import combinations
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from pilot_public_fullname_statistics import AGENT, summarize


OUTPUT = Path("data/reference/public-fullname-batch-2026-11-16.json")
PERMUTATIONS = 499
SOURCES = [
    {"id": f"msu_sa_{year}m", "institution": "МГУ", "year": year, "parser": "fio_table",
     "url": f"https://sa.cs.msu.ru/{path}"}
    for year, path in [(2020, "node/420"), (2021, "node/431"), (2022, "node/444"),
                       (2023, "node/455"), (2024, "node/466"), (2025, "2025m")]
] + [{"id": "tsu_botany_recent", "institution": "ТГУ", "parser": "year_strong_names",
      "year_from": 2010, "year_to": 2025, "url": "https://bot.tsu.ru/department/graduates/"}]


def full_name(value):
    value = " ".join(value.split()).strip(" *.,;")
    word = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    match = re.fullmatch(rf"({word})\s+({word})\s+({word})", value)
    if match and match[3].casefold().endswith(("ович", "евич", "овна", "евна", "ична", "ич")):
        return tuple(part.casefold() for part in match.groups())
    return None


class FioTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse_fio_table(html, year):
    parser = FioTables()
    parser.feed(html)
    values, eligible, rejected = [], 0, 0
    for table in parser.tables:
        index = None
        for row in table:
            if "ФИО" in row:
                index = row.index("ФИО")
                continue
            if index is None or len(row) <= index or not row[index].strip():
                continue
            eligible += 1
            candidate = full_name(row[index])
            if candidate:
                values.append(candidate)
            else:
                rejected += 1
    return {year: values}, {year: {"eligible_name_cells": eligible, "unsupported_name_cells": rejected}}


class YearStrongNames(HTMLParser):
    def __init__(self, year_from, year_to):
        super().__init__()
        self.depth = 0
        self.heading = None
        self.strong = None
        self.year = None
        self.year_from, self.year_to = year_from, year_to
        self.by_year = defaultdict(list)
        self.quality = defaultdict(lambda: {"eligible_name_cells": 0, "unsupported_name_cells": 0})

    def handle_starttag(self, tag, attrs):
        if not self.depth:
            if "entry-content" in dict(attrs).get("class", "").split():
                self.depth = 1
            return
        if tag not in {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}:
            self.depth += 1
        if tag in {"h2", "h3"}:
            self.heading = []
        elif tag == "strong" and self.heading is None:
            self.strong = []

    def handle_data(self, value):
        if self.depth:
            if self.heading is not None:
                self.heading.append(value)
            elif self.strong is not None:
                self.strong.append(value)

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in {"h2", "h3"} and self.heading is not None:
            text = " ".join("".join(self.heading).split())
            match = re.fullmatch(r"((?:19|20)[0-9]{2})(?:\s*г\.?)?", text)
            self.year = int(match[1]) if match else None
            self.heading = None
            self.strong = None
        elif tag == "strong" and self.strong is not None:
            value = " ".join("".join(self.strong).split())
            if self.year is not None and self.year_from <= self.year <= self.year_to and value:
                self.quality[self.year]["eligible_name_cells"] += 1
                candidate = full_name(value)
                if candidate:
                    self.by_year[self.year].append(candidate)
                else:
                    self.quality[self.year]["unsupported_name_cells"] += 1
            self.strong = None
        if tag not in {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}:
            self.depth -= 1


def fetch(url, limit=2000000):
    host = urlsplit(url).netloc
    if host not in {"sa.cs.msu.ru", "bot.tsu.ru"}:
        raise ValueError("Источник вне фиксированного списка")
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=20) as response:
                if urlsplit(response.geturl()).netloc != host:
                    raise ValueError("Неожиданное перенаправление")
                data = response.read(limit + 1)
                encoding = response.headers.get_content_charset() or "utf-8"
            break
        except HTTPError:
            raise  # Не повторяем отказ доступа и ограничения частоты запросов.
        except (URLError, TimeoutError):
            if attempt:
                raise
            time.sleep(1)
    if len(data) > limit:
        raise ValueError("Превышен размер страницы")
    return data, data.decode(encoding)


def run():
    results, by_institution, robots = [], defaultdict(list), {}
    cohort_labels = {}
    for source in SOURCES:
        item = dict(source)
        try:
            host = urlsplit(source["url"]).netloc
            if host not in robots:
                policy = RobotFileParser()
                try:
                    _, text = fetch(f"https://{host}/robots.txt", 200000)
                    policy.parse(text.splitlines())
                except HTTPError as error:
                    if error.code != 404:
                        raise
                    policy.parse([])
                robots[host] = policy
            if not robots[host].can_fetch(AGENT, source["url"]):
                item["status"] = "robots_disallowed"
                results.append(item)
                continue
            raw, html = fetch(source["url"])
            item["source_sha256"] = hashlib.sha256(raw).hexdigest()
            item["robots_checked"] = True
            if source["parser"] == "fio_table":
                years, quality = parse_fio_table(html, source["year"])
            else:
                parser = YearStrongNames(source["year_from"], source["year_to"])
                parser.feed(html)
                years, quality = parser.by_year, parser.quality
            item["cohorts"] = []
            for year in sorted(quality):
                triples = years.get(year, [])
                item["cohorts"].append({"year": year, "extraction": quality[year], "statistics": summarize(triples, permutations=PERMUTATIONS)})
                by_institution[source["institution"]].extend(triples)
                cohort_labels[(source["institution"], source["id"], year)] = set(triples)
            item["status"] = "processed" if any(years.values()) else "no_supported_rows"
        except (URLError, TimeoutError, ValueError, UnicodeError):
            item["status"] = "access_or_format_failed"
        results.append(item)
        print(json.dumps({"source_id": item["id"], "status": item["status"]}, ensure_ascii=False), flush=True)
    overlaps = []
    for (left, a), (right, b) in combinations(cohort_labels.items(), 2):
        common = len(a & b)
        if common:
            overlaps.append({"left": {"institution": left[0], "source_id": left[1], "year": left[2]},
                             "right": {"institution": right[0], "source_id": right[1], "year": right[2]},
                             "identical_written_fullnames": common,
                             "interpretation": "Совпадения написания; тождество людей и причина повторов не установлены"})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "sources": results,
            "coverage_complete_for_fixed_source_list": all(item["status"] == "processed" for item in results),
            "institution_pools": {institution: summarize(triples, permutations=PERMUTATIONS) for institution, triples in by_institution.items()},
            "one_per_written_fullname_sensitivity": {institution: summarize(sorted(set(triples)), permutations=PERMUTATIONS)
                                                     for institution, triples in by_institution.items()},
            "sensitivity_scope": "Только проверка влияния повторных написаний; не дедупликация по людям и не основной результат",
            "cross_cohort_written_name_overlaps": overlaps,
            "count_unit": "supported_publication_rows_not_verified_unique_people",
            "cross_institution_pooling": False, "personal_rows_saved": False, "raw_documents_saved": False,
            "limitations": ["Результаты по учреждениям не объединены в оценку населения России.",
                            "Публикации могут повторять людей; совпадение ФИО не является ключом дедупликации.",
                            "Отбор по поддержанным формам имён и по источникам смещает состав.",
                            "Возрастной охват документов не вводит возраст как атрибут разметки.",
                            "Значимость множества попарных проверок без поправки не является подтверждённым общим выводом."]}


def compact(report):
    family_size = 4 * len(report["institution_pools"])
    return {"checked_at": report["checked_at"],
            "coverage_complete_for_fixed_source_list": report.get("coverage_complete_for_fixed_source_list", False),
            "sources": [{"id": item["id"], "status": item["status"],
                         "recognized_rows": sum(c["statistics"].get("recognized_fullname_rows", 0) for c in item.get("cohorts", [])),
                         "eligible_cells": sum(c["extraction"]["eligible_name_cells"] for c in item.get("cohorts", [])),
                         "unsupported_cells": sum(c["extraction"]["unsupported_name_cells"] for c in item.get("cohorts", []))}
                        for item in report["sources"]],
            "institution_pools": {key: {"rows": value.get("recognized_fullname_rows", 0),
                                         "distinct_written_fullnames": value.get("distinct_written_fullnames", 0),
                                         "permutation_baseline": value.get("permutation_baseline", {})}
                                  for key, value in report["institution_pools"].items()},
            "cross_cohort_written_name_overlaps": report.get("cross_cohort_written_name_overlaps", []),
            "one_per_written_fullname_sensitivity": {key: value.get("permutation_baseline", {})
                                                     for key, value in report.get("one_per_written_fullname_sensitivity", {}).items()},
            "institution_pool_multiple_testing": {
                "method": "Bonferroni_for_four_pooled_statistics_per_institution_only",
                "family_size": family_size,
                "adjusted_p": {key: {name: min(1.0, item["permutation_upper_tail_p"] * family_size)
                                      for name, item in value.get("permutation_baseline", {}).items()}
                               for key, value in report["institution_pools"].items()}},
            "limitations": report["limitations"]}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        report = run()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        report = json.loads(OUTPUT.read_text(encoding="utf-8"))
    print(json.dumps(compact(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
