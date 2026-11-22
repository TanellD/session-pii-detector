"""Проверка только собственного RU-synth CONFIDE: агрегаты, без вывода исходных значений."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REVISION = "91fa72f894c83680129c460daa47426101c4d596"
FILES = ["README.md", "LICENSE", "docs/DATASHEET.md", "docs/ANNOTATION-CODEBOOK.md",
         "data/sessions-ru/SYNTHETIC-NOTICE.md", "data/sessions-ru/pii-eval-ru.jsonl",
         "data/sessions-ru/pii-adversarial-ru.jsonl"]
TURN = re.compile(r"(?m)^(?:\*\*\d{2}:\d{2}:\d{2}\s+([ТК]):\*\*|([ТК]):)\s*")


def audit(rows):
    labels, documents, integrity_errors = Counter(), Counter(), 0
    metadata_spans = 0
    overview = []
    for row in rows:
        text, spans = row["text"], row["spans"]
        turns = list(TURN.finditer(text))
        body_start = turns[0].start() if turns else 0
        body_spans = [s for s in spans if s["start"] >= body_start]
        metadata_spans += len(spans) - len(body_spans)
        labels.update(s["type"] for s in spans)
        documents.update({s["type"] for s in body_spans})
        integrity_errors += sum(text[s["start"]:s["end"]] != s["value"] for s in spans)
        types = Counter(s["type"] for s in body_spans)
        overview.append({"doc_id": row["doc_id"], "client": row["client"], "split": row["split"],
            "characters": len(text), "dialogue_turns": len(turns), "body_labels": dict(types),
            "numeric_full_phone_spans": sum(s["type"] == "PHONE" and len(re.sub(r"\D", "", s["value"])) == 11 for s in body_spans),
            "numeric_age_spans": sum(s["type"] == "AGE" and bool(re.fullmatch(r"\d{1,3}", s["value"])) for s in body_spans),
            "snils_candidates": sum(s["type"] == "ID" and "snils" in (s.get("entity_id") or "")
                                    and len(re.sub(r"\D", "", s["value"])) == 11 for s in body_spans)})
    return {"documents": len(rows), "profiles": len({r["client"] for r in rows}),
            "spans": sum(labels.values()), "source_label_counts": dict(labels),
            "body_documents_by_source_label": dict(documents), "spans_before_dialogue": metadata_spans,
            "span_value_mismatches": integrity_errors, "overview": overview}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Скачать фиксированный белый список RU-synth; реальные срезы не загружаются")
    parser.add_argument("--prepare-prefixes", action="store_true", help="Подготовить короткие исходные dev-префиксы без gold и API-запросов")
    args = parser.parse_args()
    cache = ROOT / "data/external/confide-synthetic" / REVISION
    if args.download:
        for name in FILES:
            path = cache / name
            if not path.exists():
                with urlopen(f"https://raw.githubusercontent.com/glebis/confide/{REVISION}/{name}", timeout=30) as response:
                    data = response.read()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    missing = [name for name in FILES if not (cache / name).exists()]
    if missing:
        parser.error("Нет кеша, выполните --download")
    def rows(name):
        return [json.loads(line) for line in (cache / "data/sessions-ru" / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = {"source": "https://github.com/glebis/confide", "revision": REVISION,
              "provenance": "author_declared_synthetic_not_independently_verified",
              "license": "CC-BY-4.0 for synthetic data/docs; MIT for code",
              "sha256": {name: hashlib.sha256((cache / name).read_bytes()).hexdigest() for name in FILES},
              "ru_sessions": audit(rows("pii-eval-ru.jsonl")),
              "ru_adversarial_snippets": audit(rows("pii-adversarial-ru.jsonl")),
              "ready_as_current_annotation_gold": False}
    if args.prepare_prefixes:
        target = ROOT / "data/experiments/confide-annotation-candidates-2026-11-19"
        if target.exists():
            parser.error("Каталог кандидатов уже существует; сохранённые данные не переписываются")
        sessions, origins = [], []
        for row in rows("pii-eval-ru.jsonl"):
            if row["doc_id"] not in {"ru-a-s01", "ru-c-s01", "ru-e-s01"}:
                continue
            matches = list(TURN.finditer(row["text"]))
            messages = []
            selected = matches[:16]
            boundaries = []
            for index, marker in enumerate(selected):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(row["text"])
                text = row["text"][marker.start():end].rstrip()
                messages.append({"id": f"m{index + 1}", "text": text})
                boundaries.append({"message_id": f"m{index + 1}", "start": marker.start(), "end": marker.start() + len(text)})
            session_id = "confide-" + row["doc_id"] + "-prefix16"
            sessions.append({"session_id": session_id, "messages": messages})
            origins.append({"session_id": session_id, "source_doc_id": row["doc_id"], "source_split": row["split"],
                            "message_source_offsets": boundaries})
        target.mkdir(parents=True)
        (target / "sessions.json").write_text(json.dumps({"synthetic_only": True, "sessions": sessions},
                                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (target / "provenance.json").write_text(json.dumps({"source": report["source"], "revision": REVISION,
            "license": report["license"], "synthetic_notice": "data/sessions-ru/SYNTHETIC-NOTICE.md",
            "source_gold_included_in_model_input": False, "human_review_status": "pending",
            "approved_as_gold": False, "origins": origins}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["prepared_candidates"] = {"sessions": len(sessions), "messages": sum(len(s["messages"]) for s in sessions),
                                         "output": str(target), "api_requests": 0}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
