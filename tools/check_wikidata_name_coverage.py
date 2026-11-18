"""Проверяет заполненность свойств Викиданных; получает только счётчики."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


QUERY = Path("configs/wikidata-name-coverage.sparql")
OUTPUT = Path("data/reference/wikidata-name-coverage-2026-11-16.json")
FIELDS = {"sampledPeople", "withGivenName", "withFamilyName", "withExplicitPatronym",
          "withFatherLink", "withFatherGivenName", "withAllThreeProperties", "withRecordedDeathDate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if not args.download:
        print(OUTPUT.read_text(encoding="utf-8"))
        return
    query = QUERY.read_text(encoding="utf-8")
    endpoint = "https://query.wikidata.org/sparql"
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "endpoint": endpoint,
              "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
              "scope": "Первые по URI элементы с гражданством РФ и русской статьёй; только проверка заполненности.",
              "person_rows_requested": False, "person_identifiers_saved": False,
              "warning": "Отсутствие даты смерти не доказывает, что человек жив. Наличие русской статьи и гражданство не означают место проживания или этничность."}
    try:
        request = Request(endpoint + "?" + urlencode({"query": query, "format": "json"}),
                          headers={"User-Agent": "SessionPiiResearch/0.1 (aggregate-only name-property coverage)",
                                   "Accept": "application/sparql-results+json"})
        with urlopen(request, timeout=35) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("Ответ превышает лимит")
        rows = json.loads(raw)["results"]["bindings"]
        if len(rows) != 1 or set(rows[0]) != FIELDS:
            raise ValueError("Неожиданный формат агрегата")
        counts = {}
        for key in FIELDS:
            value = rows[0][key]["value"]
            if not isinstance(value, str) or not value.isdigit():
                raise ValueError("Ожидался неотрицательный счётчик")
            counts[key] = int(value)
        if counts["sampledPeople"] > 500 or any(value > counts["sampledPeople"] for value in counts.values()):
            raise ValueError("Несогласованные счётчики")
        result.update(status="checked", counts=counts)
    except (URLError, TimeoutError, ValueError, KeyError, TypeError):
        result.update(status="access_or_query_failed", counts=None)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "checked":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
