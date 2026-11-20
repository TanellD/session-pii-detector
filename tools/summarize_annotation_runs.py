"""Агрегаты сохранённых запусков разметки без сети и вывода текстов сессий."""
import argparse
from collections import Counter
import json
from pathlib import Path
from run_compatible_annotation_pilot import response_document, materialize_quotes, checked_annotation


def summarize(path):
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    diagnostics = []
    returned_models = {r["returned_model"] for r in manifest["records"] if r.get("returned_model")}
    for number, record in enumerate(manifest["records"], 1):
        item = {"session_id": record["session_id"], "status": record["status"],
                "http_status": record.get("http_status"), "error_type": record.get("error_type")}
        response = path / f"{number:02d}-response.json"
        if response.exists():
            model = json.loads(response.read_text(encoding="utf-8")).get("model")
            if model:
                returned_models.add(model)
        if record["status"] == "invalid_or_failed" and response.exists():
            request = json.loads((path / f"{number:02d}-request.json").read_text(encoding="utf-8"))
            source = json.loads(request["messages"][1]["content"])
            try:
                doc = response_document(json.loads(response.read_text(encoding="utf-8")))
                if manifest.get("span_mode", "offsets") == "quotes":
                    doc = materialize_quotes(doc, source)
                checked_annotation(doc, source)
            except ValueError as error:
                item["validation_error"] = str(error)
        diagnostics.append(item)
    return {"run": str(path), "requested_model": manifest["requested_model"],
            "returned_models": sorted(returned_models),
            "span_mode": manifest.get("span_mode", "offsets"), "response_format": manifest["response_format"],
            "api_requests": manifest["api_requests"], "statuses": dict(Counter(r["status"] for r in manifest["records"])),
            "human_review_status": manifest["human_review_status"], "records": diagnostics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([summarize(path) for path in args.runs], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
