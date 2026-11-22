"""Локальное исследование ФИ и ФИО; кеш с ФИО разрешён пользователем только вне Git."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
import xml.etree.ElementTree as ET

from pilot_wikipedia_fullnames import explicit_triple, fetch_json, patronymic_form
from urllib.parse import unquote, urlsplit

ROOT = Path("data/private/name-dependence")
WIKI_QUERY = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?person ?article ?label WHERE {
 ?person wdt:P31 wd:Q5; wdt:P27 wd:Q159; rdfs:label ?label.
 FILTER(LANG(?label) = "ru")
 ?article schema:about ?person; schema:isPartOf <https://ru.wikipedia.org/>.
} ORDER BY ?person LIMIT 10000"""
FNS_URL = "https://file.nalog.ru/opendata/7707329152-rsmp/data-10112026-structure-12052026.zip"


def write_private(path, value):
    path = Path(path)
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Cache must stay in the private directory")
    ignored = subprocess.run(["git", "check-ignore", "--quiet", str(path)], capture_output=True)
    if ignored.returncode != 0:
        raise ValueError("Git does not ignore the cache")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def download_wiki():
    cache = ROOT / "wikipedia-10000.json"
    if cache.exists():
        return {"source": "wiki", "status": "using_existing_cache"}
    payload, digest = fetch_json("https://query.wikidata.org/sparql", {"query": WIKI_QUERY, "format": "json"})
    records, seen = [], set()
    counts = Counter()
    for row in payload["results"]["bindings"]:
        key = row["person"]["value"]
        if key in seen:
            counts["duplicate_item_rows"] += 1
            continue
        seen.add(key)
        title = unquote(urlsplit(row["article"]["value"]).path.removeprefix("/wiki/")).replace("_", " ")
        triple = explicit_triple(title, row["label"]["value"])
        if triple:
            records.append({"surname": triple[0], "name": triple[1], "patronymic": triple[2],
                            "item": key.rsplit("/", 1)[-1], "evidence": "ru_label_matches_ru_article_title"})
        else:
            counts["unsupported_or_disagrees"] += 1
    result = {"source": "wikidata_ru_labels_and_ruwiki_titles", "checked_at": datetime.now(timezone.utc).isoformat(),
              "query": WIKI_QUERY, "response_sha256": digest, "sampled_items": len(seen),
              "counts": dict(counts), "records": records,
              "scope": "URI-ordered citizenship RF; no article-text verification; not population frequencies"}
    write_private(ROOT / "wikipedia-10000.json", result)
    return {"source": result["source"], "sampled_items": len(seen), "supported_fullnames": len(records)}


class RemoteZip(io.RawIOBase):
    """Читает стандартные HTTP Range без скачивания всего архива."""
    def __init__(self, url):
        self.url, self.position, self.transferred = url, 0, 0
        request = Request(url, method="HEAD", headers={"User-Agent": "SessionPiiResearch/0.1"})
        with urlopen(request, timeout=30) as response:
            self.size = int(response.headers["Content-Length"])

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if self.position < 0:
            raise ValueError("negative position")
        return self.position

    def read(self, size=-1):
        size = min(self.size - self.position, size if size >= 0 else self.size - self.position)
        if size <= 0:
            return b""
        if size > 64_000_000 or self.transferred + size > 150_000_000:
            raise ValueError("bounded download limit")
        start, end = self.position, self.position + size - 1
        request = Request(self.url, headers={"User-Agent": "SessionPiiResearch/0.1",
                                            "Range": f"bytes={start}-{end}"})
        with urlopen(request, timeout=35) as response:
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
                raise ValueError("HTTP range unsupported")
            raw = response.read(size + 1)
        if len(raw) != size:
            raise ValueError("incomplete range")
        self.position += size
        self.transferred += size
        return raw


def parse_fns_xml(raw, seen, limit):
    records = []
    for _, node in ET.iterparse(io.BytesIO(raw), events=("end",)):
        if node.tag.rsplit("}", 1)[-1] != "Документ":
            continue
        for ip in node.iter():
            if ip.tag.rsplit("}", 1)[-1] not in ("ИПВклМСП", "СвИП"):
                continue
            identifier = ip.get("ИННФЛ") or ip.get("ИНН")
            if not identifier or identifier in seen:
                continue
            for fio in ip.iter():
                if all(fio.get(k) for k in ("Фамилия", "Имя", "Отчество")):
                    triple = tuple(fio.get(k).strip().casefold() for k in ("Фамилия", "Имя", "Отчество"))
                    seen.add(identifier)
                    records.append(dict(zip(("surname", "name", "patronymic"), triple)))
                    break
        node.clear()
        if len(records) >= limit:
            return records[:limit]
    return records


def download_fns():
    cache = ROOT / "fns-rsmp-2026-11-10.json"
    if cache.exists():
        return {"source": "fns", "status": "using_existing_cache"}
    source = RemoteZip(FNS_URL)
    records, seen, files = [], set(), []
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if not member.filename.lower().endswith(".xml"):
                continue
            if member.file_size > 100_000_000:
                continue
            raw = archive.read(member)
            records.extend(parse_fns_xml(raw, seen, 20000 - len(records)))
            files.append({"member_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
            print(json.dumps({"stage": "fns", "xml_files": len(files), "fullnames": len(records)}), flush=True)
            if len(records) >= 20000 or len(files) >= 15:
                break
    result = {"source": "fns_rsmp_individual_entrepreneurs", "url": FNS_URL,
              "checked_at": datetime.now(timezone.utc).isoformat(), "files": files,
              "downloaded_bytes": source.transferred, "records": records,
              "scope": "First readable XML members of SME snapshot, IP only, dedup INN in memory; INN not saved"}
    if not records:
        raise ValueError("no supported IP records")
    write_private(ROOT / "fns-rsmp-2026-11-10.json", result)
    return {"source": result["source"], "supported_fullnames": len(records), "downloaded_bytes": source.transferred}


def entropy(counter, n):
    return -sum(c / n * math.log2(c / n) for c in counter.values())


def descriptive(rows):
    n = len(rows)
    marginal = [Counter(r[i] for r in rows) for i in range(3)]
    pair, full = Counter(r[:2] for r in rows), Counter(rows)
    h = [entropy(c, n) for c in marginal]
    mi = h[0] + h[1] - entropy(pair, n)
    tc = sum(h) - entropy(full, n)
    supported = [(count / n) / (marginal[0][f] / n * marginal[1][i] / n)
                 for (f, i), count in pair.items() if count >= 5]
    return {"rows": n, "distinct_FI": len(pair), "distinct_FIO": len(full),
            "repeated_FI_cells": sum(c > 1 for c in pair.values()),
            "repeated_FIO_cells": sum(c > 1 for c in full.values()),
            "FI_mutual_information_bits": mi, "FIO_total_correlation_bits": tc,
            "FIO_residual_after_FI_bits": tc - mi,
            "FI_lift_for_cells_count_ge_5": {"cells": len(supported), "min": min(supported) if supported else None,
                                              "max": max(supported) if supported else None}}


def heldout(rows, strength=100, seed=42):
    rows = sorted(rows)
    random.Random(seed).shuffle(rows)
    split = int(len(rows) * .8)
    train, test = rows[:split], rows[split:]
    n = len(train)
    counts = [Counter(r[i] for r in train) for i in range(3)]
    vocab = [set(c) for c in counts]
    # UNK is an explicit held-out unseen-value category for every model.
    project = lambda r: tuple(v if v in vocab[i] else None for i, v in enumerate(r))
    pair = Counter(r[:2] for r in train)
    io_counts = Counter(r[1:] for r in train)
    full = Counter(train)
    loss = Counter()
    stratum_loss = {"FI_seen_in_train": Counter(), "FI_unseen_in_train": Counter()}
    strata = Counter()
    for original in test:
        row = project(original)
        p = [(counts[i][row[i]] + .5) / (n + .5 * (len(vocab[i]) + 1)) for i in range(3)]
        independent = math.prod(p)
        fi = (pair[row[:2]] + strength * p[0] * p[1]) / (n + strength)
        fi_times_o = fi * p[2]
        tree = fi * (io_counts[row[1:]] + strength * p[2]) / (counts[1][row[1]] + strength)
        joint = (full[row] + strength * tree) / (n + strength)
        stratum = "FI_seen_in_train" if pair[row[:2]] else "FI_unseen_in_train"
        for model, probability in (("FI_independent", p[0] * p[1]), ("FI_joint_shrunk", fi),
                                   ("independent", independent), ("FI_times_O", fi_times_o),
                                   ("FI_times_O_given_I", tree), ("joint_shrunk", joint)):
            loss[model] -= math.log2(probability)
            stratum_loss[stratum][model] -= math.log2(probability)
        strata[stratum] += 1
        strata["FIO_seen_in_train" if full[row] else "FIO_unseen_in_train"] += 1
        strata["any_part_unseen_in_train"] += any(v is None for v in row)
    return {"train_rows": n, "test_rows": len(test), "shrinkage_strength": strength,
            "mean_log_loss_bits": {k: v / len(test) for k, v in loss.items()},
            "mean_log_loss_by_FI_coverage": {group: {k: v / strata[group] for k, v in values.items()}
                                               for group, values in stratum_loss.items() if strata[group]},
            "coverage": dict(strata), "scope": "Random row holdout within source, not independent population validation"}


def pair_permutation(rows):
    gen = random.Random(42)
    actual = descriptive(rows)["FI_mutual_information_bits"]
    null = []
    for _ in range(199):
        columns = [list(c) for c in zip(*rows)]
        gen.shuffle(columns[1])
        null.append(descriptive(list(zip(*columns)))["FI_mutual_information_bits"])
    return {"observed": actual, "null_mean": sum(null) / len(null),
            "upper_tail_p": (1 + sum(v >= actual - 1e-10 for v in null)) / (1 + len(null)),
            "permutations": len(null), "seed": 42}


def analyze(rows):
    result = {"descriptive": descriptive(rows), "heldout_sensitivity": [heldout(rows, s) for s in (10, 100, 1000, 10000, 100000)],
              "FI_permutation": pair_permutation(rows)}
    result["within_written_patronymic_form"] = {}
    for form in ("masculine_form", "feminine_form"):
        subset = [r for r in rows if patronymic_form(r) == form]
        if len(subset) >= 50:
            result["within_written_patronymic_form"][form] = {
                "descriptive": descriptive(subset), "heldout": heldout(subset), "FI_permutation": pair_permutation(subset)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", choices=("wiki", "fns"))
    args = parser.parse_args()
    if args.download:
        try:
            print(json.dumps(download_wiki() if args.download == "wiki" else download_fns(), ensure_ascii=False))
        except Exception as error:
            failure = {"source": args.download, "status": "download_failed", "error_type": type(error).__name__,
                       "http_status": getattr(error, "code", None), "checked_at": datetime.now(timezone.utc).isoformat()}
            write_private(ROOT / (args.download + "-last-attempt.json"), failure)
            print(json.dumps(failure))
            raise SystemExit(2)
        return
    reports = {}
    for path in (ROOT / "wikipedia-10000.json", ROOT / "fns-rsmp-2026-11-10.json"):
        if not path.exists():
            continue
        raw = path.read_bytes()
        payload = json.loads(raw)
        rows = [tuple(r[k] for k in ("surname", "name", "patronymic")) for r in payload["records"]]
        if len(rows) >= 100:
            reports[payload["source"]] = {"cache_sha256": hashlib.sha256(raw).hexdigest(), **analyze(rows)}
    prior = {}
    for pattern in ("wikipedia-fullname-pilot-2026-11-16*.json", "wikidata-name-coverage-2026-11-16.json",
                    "public-fullname*-2026-11-16.json", "name-frequency-audit-2026-11-16.json"):
        for path in Path("data/reference").glob(pattern):
            prior[path.name] = json.loads(path.read_text(encoding="utf-8"))
    write_private(ROOT / "prior-aggregate-results.json", prior)
    write_private(ROOT / "results.json", reports)
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
