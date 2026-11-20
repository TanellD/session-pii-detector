"""Check adapter transformations on synthetic attributes without t_gold."""

import argparse
import json
from pathlib import Path

from .adapter import extract

FIXTURE = Path("benchmarks/pii_smoke/data/synthetic-attributes.json")


def _extract(case_text, with_model):
    if with_model:
        from .adapter_gliner import extract_message

        return extract_message(case_text)
    return extract(case_text)


def check(fixture_path=FIXTURE, with_model=False):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if payload.get("synthetic") is not True:
        raise ValueError("Adapter fixture must be synthetic")
    if "t_gold" in payload:
        raise ValueError("Adapter fixture must not contain t_gold")

    failures = []
    results = []
    for case in payload["cases"]:
        requires = case.get("requires", "any")
        if requires == "model" and not with_model:
            results.append({"id": case["id"], "skipped": "requires model"})
            continue
        text = case["text"]
        mentions = _extract(text, with_model)
        got_types = [m.type for m in mentions]

        for m in mentions:
            if not 0 <= m.start < m.end <= len(text):
                failures.append(
                    f"{case['id']}: invalid span {m.type} [{m.start}:{m.end}]"
                )
            if m.text != text[m.start : m.end]:
                failures.append(f"{case['id']}: text mismatch for {m.type}")

        expected = case.get("transformation_hint", [])
        if expected and sorted(got_types) != sorted(expected):
            failures.append(f"{case['id']}: expected {expected}, got {got_types}")

        # Stable ID: same input twice → same IDs
        again = _extract(text, with_model)
        if [m.id for m in mentions] != [m.id for m in again]:
            failures.append(f"{case['id']}: unstable mention IDs between runs")

        results.append(
            {
                "id": case["id"],
                "text": text,
                "expected_types": expected,
                "mentions": [m.to_dict() for m in mentions],
            }
        )

    return {
        "contract_version": payload["contract_version"],
        "with_model": with_model,
        "cases": results,
        "failures": failures,
        "ok": not failures,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--with-model", action="store_true")
    args = parser.parse_args()

    report = check(args.input, with_model=args.with_model)
    payload = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(payload)

    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
