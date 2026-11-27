"""Read-only consistency probes on synthetic cases and local manifest hashes."""
import hashlib
import json
from pathlib import Path

from annotation_calculator import Calculator
from annotation_contract import ANNOTATION_TYPES, assess
from build_llm_annotation_pilot import CASES
from estimate_address_population import estimate_resolved
from annotation_address_enrichment import build_enrichment

ROOT = Path(__file__).resolve().parents[1]


def document(case):
    name, texts, labels, example, uncertain, _ = case
    result = {"schema_version": "annotation-mvp-v2", "session_id": name,
              "messages": [{"id": f"m{i}", "text": text} for i, text in enumerate(texts, 1)],
              "entities": [], "case_flags": {"is_example": example,
              "annotation_uncertain": uncertain is not None, "clarification_reason": uncertain}}
    for i, (number, value, kind, person) in enumerate(labels, 1):
        start = texts[number - 1].index(value)
        result["entities"].append({"id": f"e{i}", "message_id": f"m{number}",
            "start": start, "end": start + len(value), "type": kind, "person_id": person})
    return result


def main():
    calculator = Calculator(policy_only=True)
    result = assess(document(next(c for c in CASES if c[0] == "pair_then_email")), calculator)
    first = result["first_alert"]
    prefix_unknown = [m["message_id"] for m in result["messages"]
                      if m["assessment"] == "insufficient_data"]
    address_case = ("synthetic_address_numeric_gap",
        ["Testimir", "Synthetic city, Example street, house 1, apartment 2"],
        [(1, "Testimir", "first_name", "p1"),
         (2, "Synthetic city, Example street, house 1, apartment 2", "address", "p1")],
        False, None, None)
    annotation_result = assess(document(address_case), calculator)
    preview = estimate_resolved(calculator, {"value": address_case[1][1],
        "completeness": "full", "has_flat": True, "flat_type": "кв", "resolved_level": "apartment"})
    enriched_result = calculator.person([
        {"type": "first_name", "value": "Testimir", "person_id": "p1", "complete": True},
        {**preview["address_fact"], "person_id": "p1"}])
    address_doc = document(address_case)
    address_stage = build_enrichment(address_doc, calculator, lambda _: preview)
    json_enriched = assess(address_doc, calculator, address_enrichment=address_stage)
    manifests = []
    base = ROOT / "data/experiments/llm-annotation-pilot-2026-11-18"
    for relative in ("manifest.json", "refined-policy/manifest.json"):
        path = base / relative
        if not path.exists():
            manifests.append({"manifest": relative, "status": "absent"})
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        current_policy = hashlib.sha256((ROOT / "configs/annotation-estimator-policy.json").read_bytes()).hexdigest()
        changed_prompts = [name for name, sha in payload.get("prompt_sha256", {}).items()
                          if hashlib.sha256((ROOT / "docs/data" / name).read_bytes()).hexdigest() != sha]
        manifests.append({"manifest": relative, "policy_matches_current": payload.get("policy_sha256") == current_policy,
                          "changed_prompt_files": changed_prompts})
    print(json.dumps({"synthetic_only": True, "annotation_types": sorted(ANNOTATION_TYPES),
        "first_moment_probe": {"unknown_prefix_messages": prefix_unknown,
            "reported_first_alert": first, "reported_case_flags": result["case_flags"]},
        "address_path_probe": {"annotation_session_alert": annotation_result["session_alert"],
            "annotation_uncertain": annotation_result["case_flags"]["annotation_uncertain"],
            "enriched_person_alert": enriched_result["alert"],
            "json_with_address_stage_alert": json_enriched["session_alert"],
            "enriched_reason": enriched_result["alert_reason"]},
        "local_manifests": manifests}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
