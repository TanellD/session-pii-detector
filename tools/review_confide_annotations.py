"""Сборка и проверка отдельной переразметки CONFIDE из точных LLM-фрагментов.

Сеть и API не используются. Исходные документы и выбранные значения остаются
в игнорируемом data/. Это проверка согласованности, не оценка качества модели.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from annotation_calculator import Calculator
from annotation_contract import assess, validate
from run_compatible_annotation_pilot import materialize_quotes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/experiments/confide-annotation-candidates-2026-11-19"
DEFAULT_OUTPUT = ROOT / "data/experiments/confide-annotation-review-2026-11-19"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(candidate_dir, output_dir):
    source_path = candidate_dir / "sessions.json"
    provenance_path = candidate_dir / "provenance.json"
    selection_path = output_dir / "selections.json"
    source, provenance, selections = read(source_path), read(provenance_path), read(selection_path)
    if source.get("synthetic_only") is not True:
        raise ValueError("Нужен явно синтетический вход")
    sessions = {s["session_id"]: s for s in source["sessions"]}
    chosen = selections["sessions"]
    if len(sessions) != len(source["sessions"]) or len(chosen) != len(sessions) or {s["session_id"] for s in chosen} != set(sessions):
        raise ValueError("Наборы сессий должны совпадать без дублей")
    calculator = Calculator()
    annotations, assessments = [], []
    for item in chosen:
        session = sessions[item["session_id"]]
        entities = [dict(id=f"e{i}", message_id=m, type=t, mention=v, person_id=p, occurrence=n)
                    for i, (m, t, v, p, n) in enumerate(item["selections"], 1)]
        doc = materialize_quotes({**session, "schema_version": "annotation-quotes-v1",
                                  "entities": entities, "case_flags": item["case_flags"]}, session)
        validate(doc)
        annotations.append(doc)
        assessments.append(assess(doc, calculator))

    gold_path = ROOT / "data/external/confide-synthetic" / provenance["revision"] / "data/sessions-ru/pii-eval-ru.jsonl"
    source_rows = {r["doc_id"]: r for r in map(json.loads, gold_path.read_text(encoding="utf-8").splitlines())}
    by_session = {d["session_id"]: d for d in annotations}
    differences, source_counts, categories = [], Counter(), Counter()
    for origin in provenance["origins"]:
        doc, original = by_session[origin["session_id"]], source_rows[origin["source_doc_id"]]
        for boundary in origin["message_source_offsets"]:
            message = next(m for m in doc["messages"] if m["id"] == boundary["message_id"])
            if original["text"][boundary["start"]:boundary["end"]] != message["text"]:
                raise ValueError("Исходный текст изменён")
            spans = [s for s in original["spans"] if boundary["start"] <= s["start"] and s["end"] <= boundary["end"]]
            revised = [e for e in doc["entities"] if e["message_id"] == message["id"]]
            for span in spans:
                start, end = span["start"] - boundary["start"], span["end"] - boundary["start"]
                if original["text"][span["start"]:span["end"]] != span["value"]:
                    raise ValueError("Повреждён исходный спан")
                overlap = [e for e in revised if e["start"] < end and start < e["end"]]
                category = "split" if len(overlap) > 1 else "mapped" if overlap else "omitted"
                source_counts[span["type"]] += 1
                categories[category] += 1
                differences.append({"session_id": doc["session_id"], "message_id": message["id"],
                    "source_type": span["type"], "start": start, "end": end,
                    "category": category, "revised_entity_ids": [e["id"] for e in overlap]})
    summary = {"sessions": len(annotations), "messages": sum(len(d["messages"]) for d in annotations),
        "entities": sum(len(d["entities"]) for d in annotations),
        "entity_types": dict(sorted(Counter(e["type"] for d in annotations for e in d["entities"]).items())),
        "unknown_owner_entities": sum(e["person_id"] is None for d in annotations for e in d["entities"]),
        "source_spans_in_prefixes": sum(source_counts.values()), "source_types": dict(sorted(source_counts.items())),
        "source_span_disposition": dict(sorted(categories.items())),
        "first_alerts": [{"session_id": a["session_id"], "first_alert": a["first_alert"], "case_flags": a["case_flags"]} for a in assessments],
        "validation": "passed", "api_requests": 0, "approved_as_gold": False,
        "metrics": "not_evaluated_no_independent_gold"}
    paths = [source_path, provenance_path, selection_path, gold_path, Path(__file__).resolve(),
             ROOT / "tools/annotation_contract.py", ROOT / "tools/annotation_calculator.py",
             ROOT / "tools/run_compatible_annotation_pilot.py",
             ROOT / "configs/annotation-estimator-policy.json", ROOT / "configs/annotation-classes.json",
             ROOT / "docs/data/annotation-guidelines.md", ROOT / "docs/data/annotation-class-fields.md",
             ROOT / "docs/data/schema.md"]
    paths += sorted((ROOT / "data/reference").glob("*.json"))
    manifest = {"source": provenance, "annotator": selections["annotator"],
        "human_review_status": "pending", "approved_as_gold": False,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "policy_version": calculator.policy["version"], "taxonomy_version": calculator.policy["taxonomy_version"],
        "sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in paths},
        "limitations": ["Only selected dev prefixes; not the whole CONFIDE corpus",
                         "Written-out numbers unsupported; earlier first alerts require adjudication",
                         "Original source annotations were inspected; this is not a blinded evaluation"]}
    return {"annotations.json": {"sessions": annotations}, "assessments.json": {"sessions": assessments},
            "review-notes.json": {"sessions": [{"session_id": s["session_id"], "review_notes": s["review_notes"]} for s in chosen],
                                  "source_span_mapping": differences},
            "summary.json": summary, "manifest.json": manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Проверить сохранённые результаты без перезаписи")
    args = parser.parse_args()
    products = build(args.input, args.output)
    if args.check:
        # Коммит мог измениться после сохранения. Содержимое проверяется по хешам.
        products["manifest.json"]["commit"] = read(args.output / "manifest.json")["commit"]
        if any(read(args.output / name) != value for name, value in products.items()):
            raise ValueError("Сохранённый результат не соответствует входам, правилам или коду")
    else:
        if any((args.output / name).exists() for name in products):
            parser.error("Результаты уже существуют; используйте --check или новый каталог")
        for name, value in products.items():
            (args.output / name).write_text(encoded(value), encoding="utf-8")
    print(encoded(products["summary.json"]))


if __name__ == "__main__":
    main()
