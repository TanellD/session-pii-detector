"""Ограниченный пилот Википедии: персональные ответы только в памяти, на диске сводка."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import urlencode, unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pilot_public_fullname_statistics import summarize

OUTPUT = Path("data/reference/wikipedia-fullname-pilot-2026-11-16.completed.json")
LATEST = Path("data/reference/wikipedia-fullname-pilot-2026-11-16.latest.json")
QUERY = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?person ?article ?given ?family ?patronym ?father ?fatherName WHERE {
 { SELECT DISTINCT ?person ?article WHERE {
   ?person wdt:P31 wd:Q5; wdt:P27 wd:Q159.
   ?article schema:about ?person; schema:isPartOf <https://ru.wikipedia.org/>.
 } ORDER BY ?person LIMIT 500 }
 BIND(EXISTS { ?person wdt:P735 ?g } AS ?given)
 BIND(EXISTS { ?person wdt:P734 ?f } AS ?family)
 BIND(EXISTS { ?person wdt:P5056 ?p } AS ?patronym)
 OPTIONAL { ?person wdt:P22 ?father.
   OPTIONAL { ?father wdt:P735 ?fn. ?fn rdfs:label ?fatherName.
              FILTER(LANG(?fatherName) = "ru") } }
}"""
WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
PREFIX = re.compile(rf"^\s*({WORD})\s+({WORD})\s+({WORD})(?=\s|[,.(—–-]|$)")
SUFFIXES = ("ович", "евич", "овна", "евна", "ична", "ич")


def normalize(text):
    # Ударения в энциклопедии не являются частью имени; ё сохраняется.
    return unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", text)
                                               if c != "\u0301"))


def explicit_triple(title, intro):
    """Только три начальных слова + согласование с заголовком; не общий NER."""
    match = PREFIX.match(normalize(intro))
    if not match:
        return None
    parts = tuple(p.casefold() for p in match.groups())
    if parts[1].endswith(SUFFIXES) and not parts[2].endswith(SUFFIXES):
        triple = (parts[2], parts[0], parts[1])
    elif parts[2].endswith(SUFFIXES) and not parts[1].endswith(SUFFIXES):
        triple = parts
    else:
        return None
    title = normalize(title).split(" (")[0].replace(",", " ").casefold().split()
    # Для короткого заголовка должны совпадать фамилия и имя, не произвольные две части.
    expected = Counter(triple if len(title) == 3 else triple[:2])
    return triple if len(title) in (2, 3) and Counter(title) == expected else None


def patronymic_form(triple):
    """Форма написания, не подтверждённый пол человека."""
    if triple[2].endswith(("овна", "евна", "ична")):
        return "feminine_form"
    if triple[2].endswith(("ович", "евич", "ич")):
        return "masculine_form"
    return "unsupported_form"


def conditional_diagnostics(triples, permutations=499):
    result = {}
    for form in ("feminine_form", "masculine_form"):
        rows = [row for row in triples if patronymic_form(row) == form]
        if len(rows) < 2:
            result[form] = {"rows": len(rows), "status": "insufficient_rows"}
            continue
        report = summarize(rows, permutations=permutations, seed=42)
        result[form] = {"rows": len(rows), "name_patronymic_mi": report["permutation_baseline"]["name_patronymic_mi"]}
    return result


def fetch_json(endpoint, params):
    request = Request(endpoint + "?" + urlencode(params), headers={
        "User-Agent": "SessionPiiResearch/0.1 (bounded in-memory biography study)",
        "Accept": "application/json"})
    with urlopen(request, timeout=35) as response:
        if urlsplit(response.geturl()).hostname != urlsplit(endpoint).hostname:
            raise ValueError("redirect")
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("response size")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def collect():
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "query_sha256": hashlib.sha256(QUERY.encode()).hexdigest(),
              "scope": "First 500 URI-ordered humans, Russian citizenship, Russian Wikipedia article",
              "personal_rows_saved": False, "raw_responses_saved": False,
              "person_identifiers_saved": False, "national_frequency_use": "not_accepted"}
    people = {}
    try:
        payload, digest = fetch_json("https://query.wikidata.org/sparql", {"query": QUERY, "format": "json"})
        result["wikidata_response_sha256"] = digest
        for row in payload["results"]["bindings"]:
            key = row["person"]["value"]
            if key not in people:
                article = row["article"]["value"]
                if urlsplit(article).hostname != "ru.wikipedia.org":
                    raise ValueError("article host")
                people[key] = {"title": unquote(urlsplit(article).path.removeprefix("/wiki/")).replace("_", " "),
                               "all_properties": all(row[field]["value"] == "true" for field in ("given", "family", "patronym")),
                               "fathers": set(), "father_names": set()}
            if "father" in row:
                people[key]["fathers"].add(row["father"]["value"])
            if "fatherName" in row:
                people[key]["father_names"].add(row["fatherName"]["value"].casefold())
        if not 0 < len(people) <= 500:
            raise ValueError("cohort size")
    except Exception as error:
        # Не экспортируем исключение: оно может содержать URL с именем человека.
        return dict(result, status="wikidata_request_failed", error_type=type(error).__name__,
                    http_status=getattr(error, "code", None))
    counts = Counter(sampled_items=len(people), with_all_three_properties=sum(p["all_properties"] for p in people.values()),
                     with_father_link=sum(bool(p["fathers"]) for p in people.values()))
    print(json.dumps({"stage": "cohort_loaded", "items": len(people)}), file=sys.stderr, flush=True)
    triples, father_pairs, hashes = [], [], []
    records = list(people.values())
    for start in range(0, len(records), 20):
        batch = records[start:start + 20]
        stop_access = False
        try:
            payload, digest = fetch_json("https://ru.wikipedia.org/w/api.php", {
                "action": "query", "format": "json", "formatversion": 2,
                "prop": "extracts", "exintro": 1, "explaintext": 1,
                "exlimit": 20, "titles": "|".join(p["title"] for p in batch)})
            if "error" in payload:
                raise ValueError("api error")
            hashes.append(digest)
            pages = {p["title"]: p for p in payload["query"]["pages"]}
            for record in batch:
                page = pages.get(record["title"])
                if not page or "extract" not in page:
                    counts["missing_extract"] += 1
                    continue
                counts["articles_with_extract"] += 1
                triple = explicit_triple(page["title"], page["extract"])
                if triple is None:
                    counts["unsupported_intro"] += 1
                    continue
                triples.append(triple)
                counts["explicit_triples_with_all_three_properties"] += record["all_properties"]
                if len(record["fathers"]) == 1 and len(record["father_names"]) == 1:
                    father_pairs.append((triple[1], next(iter(record["father_names"]))))
            counts["successful_batches"] += 1
        except Exception as error:
            counts["failed_batches"] += 1
            counts["batch_error_" + type(error).__name__] += 1
            if isinstance(error, HTTPError):
                counts["http_status_" + str(error.code)] += 1
                stop_access = error.code in (401, 403, 429)
        print(json.dumps({"stage": "articles", "processed": start + len(batch),
                          "recognized": len(triples), "failed_batches": counts["failed_batches"]}),
              file=sys.stderr, flush=True)
        if stop_access or (counts["failed_batches"] >= 2 and not counts["successful_batches"]):
            counts["unrequested_items_after_access_failure"] = len(records) - start - len(batch)
            break
        time.sleep(1)
    summary = summarize(triples, permutations=499, seed=42)
    if triples:
        summary["limitations"] = [
            "Узкий автоматический разбор начала статьи, без персональной ручной валидации.",
            "Единица — элемент Викиданных, не доказанно уникальный человек; совпадения ФИО не объединяются.",
            "Биографии не представляют жителей России; родственные и профессиональные кластеры не контролируются.",
            "Проверка перестановками не устраняет систематическую ошибку отбора.",
            "Отчество наблюдается в тексте, не генерируется из имени отца; семейная связь не проверена по первоисточнику."]
    return dict(result, status="completed" if not counts["failed_batches"] else "partial_article_access",
                counts=dict(counts), article_response_sha256=hashes, fullname_statistics=summary,
                conditional_on_written_patronymic_form=conditional_diagnostics(triples),
                father_pair_coverage={"unambiguous_link_and_name_with_supported_child_fullname": len(father_pairs),
                                      "distinct_written_pairs": len(set(father_pairs)),
                                      "inferred_patronymics": 0,
                                      "dependency_coefficient": None,
                                      "reason": "Coverage only; family clusters and source claims not validated"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--latest-attempt", action="store_true", help="Последняя попытка, включая частичную")
    args = parser.parse_args()
    if args.download:
        result = collect()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        LATEST.write_text(serialized, encoding="utf-8")
        if result["status"] == "completed":
            OUTPUT.write_text(serialized, encoding="utf-8")
        print(serialized)
        if result["status"] != "completed":
            raise SystemExit(2)
    else:
        print((LATEST if args.latest_attempt else OUTPUT).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
