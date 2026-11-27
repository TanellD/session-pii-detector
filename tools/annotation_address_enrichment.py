"""Явный адресный этап JSON-разметки и воспроизводимый результат без сети."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from annotation_calculator import ROOT, Calculator, address_suggestions
from estimate_address_population import estimate_from_query


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def build_enrichment(document, calculator, resolver=None):
    from annotation_contract import validate
    texts = validate(document)
    if resolver is None:
        resolver = lambda query: estimate_from_query(calculator, query, address_suggestions)
    results, cache = {}, {}
    for entity in document["entities"]:
        if entity["type"] != "address" or entity["person_id"] is None:
            continue
        query = texts[entity["message_id"]][entity["start"]:entity["end"]]
        if query not in cache:
            cache[query] = resolver(query)
        result = cache[query]
        item = {"status": result["status"], "reason": result.get("reason")}
        if result["status"] == "estimated":
            item.update(fact={key: value for key, value in result["address_fact"].items()
                             if key in {"housing_type", "house_flat_count", "reference_region", "resolved_level"}},
                        reference_date=result.get("reference_date"), reference_url=result.get("reference_url"),
                        assumptions=result.get("assumptions", []), source=result.get("building", {}).get("source"))
        results[entity["id"]] = item
    return {"version": "address-enrichment-v1", "annotation_sha256": digest(document),
            "policy_sha256": digest(calculator.policy), "housing_reference_sha256": digest(calculator.housing),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "results": results}


def apply_enrichment(document, calculator, facts, enrichment):
    if (enrichment.get("version") != "address-enrichment-v1"
            or enrichment.get("annotation_sha256") != digest(document)
            or enrichment.get("policy_sha256") != digest(calculator.policy)
            or enrichment.get("housing_reference_sha256") != digest(calculator.housing)):
        raise ValueError("Адресный этап относится к другой аннотации или политике")
    results = enrichment.get("results")
    if not isinstance(results, dict):
        raise ValueError("Нужны результаты адресного этапа")
    addresses = {f["entity_id"] for f in facts if f["type"] == "address" and f["person_id"] is not None}
    if not set(results) <= addresses:
        raise ValueError("Адресный этап ссылается на чужую сущность")
    enriched = []
    for fact in facts:
        item = results.get(fact["entity_id"], {})
        extra = item.get("fact", {}) if item.get("status") == "estimated" else {}
        if (not isinstance(extra, dict) or not set(extra) <= {
                "housing_type", "house_flat_count", "reference_region", "resolved_level"}
                or extra and extra.get("housing_type") not in {"apartment", "individual_house", "apartment_building"}):
            raise ValueError("Неверные поля адресного этапа")
        enriched.append({**fact, **extra})
    return enriched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy-only", action="store_true")
    args = parser.parse_args()
    target = args.output.resolve()
    private_root = (ROOT / "data").resolve()
    if (not target.is_relative_to(private_root) or target.exists()
            or subprocess.run(["git", "check-ignore", "--quiet", str(target)], cwd=ROOT).returncode != 0):
        parser.error("Укажите новый игнорируемый файл внутри data/; существующие результаты не перезаписываются")
    result = build_enrichment(json.loads(args.input.read_text(encoding="utf-8")), Calculator(policy_only=args.policy_only))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "saved", "address_entities": len(result["results"])}))


if __name__ == "__main__":
    main()
