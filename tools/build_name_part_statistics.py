"""Частоты отдельных Ф/И/О внутри локальных корпусов; написания только в приватном кеше."""

from collections import Counter
import hashlib
import json

from research_name_pairs import ROOT, write_private


def frequencies(records):
    denominator = len(records)
    if not denominator:
        raise ValueError("Пустой корпус")
    parts = {}
    for kind in ("surname", "name", "patronymic"):
        counts = Counter(" ".join(row[kind].casefold().replace("ё", "е").split()) for row in records)
        if not all(counts):
            raise ValueError("Для этой версии нужен одинаковый знаменатель полных троек")
        parts[kind] = [{"text": text, "count": count, "source_probability": count / denominator}
                       for text, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    return {"source_records": denominator, "parts": parts,
            "scope": "conditional on source inclusion and complete triple; not Russian population probabilities",
            "population_estimate": None, "annotation_frequency_use": "research_only"}


def main():
    summary = []
    for source, path in (("fns", ROOT / "fns-rsmp-2026-11-10.json"),
                         ("sro", ROOT / "sro-cadastral-minimal-2026-11-17.json")):
        if not path.exists():
            summary.append({"source": source, "status": "cache_missing"})
            continue
        raw = path.read_bytes()
        result = {"source": source, "source_cache_sha256": hashlib.sha256(raw).hexdigest(),
                  **frequencies(json.loads(raw)["records"])}
        target = ROOT / f"{source}-name-part-frequencies-2026-11-17.json"
        write_private(target, result)
        summary.append({"source": source, "status": "private_frequencies_written", "source_records": result["source_records"],
                        "distinct_by_part": {kind: len(rows) for kind, rows in result["parts"].items()}})
    print(json.dumps({"sources": summary, "real_names_printed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
