"""Проверить доступность источников без сохранения персональных записей."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


TARGETS = {
    "rosstat_population_catalog": "https://rosstat.gov.ru/compendium/document/13282",
    "rosstat_demography_catalog": "https://rosstat.gov.ru/folder/12781",
    "rosstat_labor_force_catalog": "https://www.rosstat.gov.ru/folder/210/document/13211",
    "hh_vacancies": "https://api.hh.ru/vacancies?area=113&per_page=1&clusters=true",
    "name_frequency_files": "https://api.github.com/repos/datacoon/russiannames/git/trees/master?recursive=1",
    "name_frequency_schema": "https://data.apicrafter.ru/tables/russiannames/names/schema",
}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            self.current = [href, ""]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def probe(item):
    key, url = item
    result = {"source": key, "url": url}
    try:
        with urlopen(Request(url, headers={"User-Agent": "annotation-research/1.0"}), timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            result["status"] = response.status
        if len(raw) > 8 * 1024 * 1024:
            return dict(result, error="response_too_large")
        if key == "hh_vacancies":
            data = json.loads(raw)
            result["found"] = data.get("found")
            result["clusters"] = [{"id": c.get("id"), "groups": len(c.get("items", []))}
                                  for c in data.get("clusters", [])]
            # Тексты вакансий, работодатели и контакты не выводятся и не сохраняются.
        elif key == "name_frequency_files":
            data = json.loads(raw)
            result["files"] = [{"path": r["path"], "size": r.get("size"), "sha": r["sha"]}
                               for r in data.get("tree", []) if r["path"].startswith("data/")]
            result["truncated"] = data.get("truncated")
        else:
            parser = Links()
            parser.feed(raw.decode("utf-8", errors="replace"))
            if key.startswith("rosstat_"):
                result["publications"] = [{"title": " ".join(title.split()), "url": urljoin(url, href)}
                                          for href, title in parser.links
                                          if ("2026" in title or "2025" in title)
                                          and ("численност" in title.lower() or "населени" in title.lower())]
                result["file_links"] = [urljoin(url, href) for href, title in parser.links
                                        if "/storage/mediabank/" in href][:60]
            else:
                result["schema_links"] = [urljoin(url, href) for href, title in parser.links if "schema.json" in title]
    except HTTPError as error:
        result.update(status=error.code, error="http_error")
    except (URLError, TimeoutError, ValueError) as error:
        result["error"] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=TARGETS, action="append")
    args = parser.parse_args()
    selected = {key: TARGETS[key] for key in args.source} if args.source else TARGETS
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(probe, selected.items()):
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
