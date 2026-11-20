"""Контекст LLM из того же конфига, который читает обработчик; без секретов и персональных записей."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "configs/annotation-estimator-policy.json"


def build_context():
    policy_bytes = POLICY_PATH.read_bytes()
    policy = json.loads(policy_bytes)
    return {"context_version": "annotation-context-v1", "policy": policy,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "computation_owner": "annotation_contract_and_annotation_calculator",
        "llm_task": "extract_entities_and_person_links; do_not_invent_population_coefficients",
        "availability": {
            "active_calculator_policy": True,
            "prepared_size_means_connected": policy["size_fallbacks"]["status"] == "connected_to_estimator",
            "name_frequencies_for_annotation": policy["name_frequency_use"] != "research_only_not_annotation_ground_truth",
            "street_population_coefficient": policy["address"]["partial_coefficient"]},
        "interpretation": [
            "A null person_id is not by itself uncertainty about the alert verdict",
            "Unlinked service contacts cannot supply a personal alert condition",
            "Explicit first-person ownership is allowed; pronoun chains are not resolved",
            "Uncertainty is a retained clarification marker; without a sufficient rule the binary alert is false",
            "Prepared size means are not active calculator rules until connected",
            "Previous prefix uncertainty and input annotation flags are preserved after later alerts",
            "Age is yellow; the population age reference is not yet connected"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = build_context()
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status": "exported", "policy_sha256": result["policy_sha256"]}))
