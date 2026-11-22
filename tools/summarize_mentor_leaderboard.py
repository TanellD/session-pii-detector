"""Read aggregate metadata from the supplied HTML, without exporting examples."""

import argparse
from collections import defaultdict
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


class ScriptReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.blocks = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/json":
            self.current = [attrs.get("id"), []]

    def handle_data(self, text):
        if self.current is not None:
            self.current[1].append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            key, chunks = self.current
            self.blocks[key] = json.loads("".join(chunks))
            self.current = None


def summarize(path):
    raw = path.read_bytes()
    parser = ScriptReader()
    parser.feed(raw.decode("utf-8"))
    board = parser.blocks["lb-data"]
    reports = [value for key, value in parser.blocks.items() if key.startswith("rep-")]
    rows = board["rows"]
    datasets = {}
    supports = defaultdict(lambda: {"supported": set(), "unsupported": set()})
    checks = []
    for report in reports:
        meta = report["meta"]
        dataset = meta["dataset"]
        name = dataset["name"]
        info = {key: dataset.get(key) for key in (
            "docs", "entities", "labels", "private", "fingerprint", "source", "repo"
        )}
        if name in datasets and datasets[name] != info:
            checks.append(f"Dataset metadata differ between reports: {name}")
        datasets[name] = info
        model = meta["model"]["name"]
        for run in report["runs"]:
            supports[model]["supported"].update(run.get("supported_labels", []))
        supports[model]["unsupported"].update(meta.get("unsupported_labels", []))
    per_dataset_gold = defaultdict(lambda: defaultdict(set))
    original_scores = []
    for row in rows:
        if row["variant"] != "original":
            continue
        report = parser.blocks["rep-" + row["run_id"]]
        # Report runs follow the explicitly recorded variant order.
        variants = report["meta"]["variants"]
        original = report["runs"][variants.index("original")]
        for label, schemas in original["per_tag"].items():
            per_dataset_gold[row["dataset"]][label].add(schemas["strict"]["possible"])
        original_scores.append({
            "dataset": row["dataset"], "model": row["model"],
            "device": row["device"], "ms_per_doc": row["ms_per_doc"],
            "coverage": row["coverage"],
            "strict_f2": original["overall"]["strict"]["f2"],
            "strict_recall": original["overall"]["strict"]["recall"],
        })
    gold = {name: {label: sorted(counts) for label, counts in sorted(labels.items())}
            for name, labels in sorted(per_dataset_gold.items())}
    for name, labels in gold.items():
        for label, counts in labels.items():
            if len(counts) != 1:
                checks.append(f"Gold count differs between models: {name}/{label}: {counts}")
    return {
        "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "report_count": len(reports), "row_count": len(rows),
        "models": sorted({row["model"] for row in rows}),
        "variants": sorted({row["variant"] for row in rows}),
        "datasets": dict(sorted(datasets.items())),
        "all_labels": sorted({label for info in datasets.values() for label in info["labels"]}),
        "support": {model: {key: sorted(values) for key, values in entry.items()}
                    for model, entry in sorted(supports.items())},
        "gold_mentions_by_dataset": gold,
        "original_strict_scores": sorted(original_scores, key=lambda r: (r["dataset"], -r["strict_f2"])),
        "consistency_warnings": checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("_drafts/leaderboard.html"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Reports: {result['report_count']}; models: {len(result['models'])}; datasets: {len(result['datasets'])}; variants: {len(result['variants'])}")
    print(f"Label union: {len(result['all_labels'])}")
    print("Models: " + ", ".join(result["models"]))
    print("Variants: " + ", ".join(result["variants"]))
    for name, info in result["datasets"].items():
        print(f"{name}: docs={info['docs']}; entities={info['entities']}; private={info['private']}")
        print("  labels: " + ", ".join(info["labels"]))
    print("Checks: " + ("; ".join(result["consistency_warnings"]) or "aggregate metadata and gold counts consistent"))
    print("No raw document text or example spans are exported.")


if __name__ == "__main__":
    main()
