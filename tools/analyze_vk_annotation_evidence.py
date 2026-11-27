"""Офлайн-исследование достаточности кеша ФИ для разметки; только обезличенные отчёты."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import re
from pathlib import Path

from assess_vk_pair_sample import observed_lift
from collect_vk_group_members import current_page
from research_name_pairs import ROOT, write_private

ALPHAS = (0, .25, .5, .75, .9, .99)


def frequencies(records):
    pairs = Counter((r["surname"], r["name"]) for r in records)
    n = len(records)
    return {"accounts": n, "distinct_pairs": len(pairs), "max_pair_count": max(pairs.values(), default=0),
            "singleton_pairs": sum(c == 1 for c in pairs.values()),
            "singleton_account_share": sum(c == 1 for c in pairs.values()) / n if n else None,
            "sex_counts": dict(Counter(str(r.get("sex")) for r in records)),
            "country_counts": dict(Counter(str(r.get("country_id")) for r in records)),
            "frequency_tiers": [{"minimum_count": k, "pair_cells": sum(c >= k for c in pairs.values()),
                                 "accounts": sum(c for c in pairs.values() if c >= k),
                                 "account_share": sum(c for c in pairs.values() if c >= k) / n if n else None}
                                for k in (2, 5, 10, 20, 50, 100)],
            "observed_lift": observed_lift(list(pairs.elements()))}


def fit_common_pairs(rows, minimum_count):
    models = {}
    for sex in (None, 1, 2):
        subset = [(f, i) for s, f, i in rows if s == sex]
        if not subset:
            models[sex] = {}
            continue
        n = len(subset)
        pairs = Counter(subset)
        first, second = Counter(f for f, i in subset), Counter(i for f, i in subset)
        models[sex] = {pair: (first[pair[0]] * second[pair[1]] / n ** 2, count / n)
                       for pair, count in pairs.items() if count >= minimum_count}
    return models


def score_common_pairs(rows, models, alpha):
    """Normalized probability space: training-selected literal pairs plus one OTHER bin, conditional on sex."""
    complements = {sex: (max(0, 1 - sum(p for p, q in cells.values())),
                         max(0, 1 - sum(q for p, q in cells.values()))) for sex, cells in models.items()}
    loss, selected, zero = 0, 0, 0
    for sex, first, second in rows:
        cells = models[sex]
        pair = (first, second)
        selected += pair in cells
        p, q = cells.get(pair, complements[sex])
        probability = (1 - alpha) * p + alpha * q
        if probability <= 0:
            zero += 1
        else:
            loss -= math.log2(probability)
    return {"mean_log_loss_bits": loss / len(rows) if rows and not zero else None,
            "selected_pair_rows": selected, "other_rows": len(rows) - selected, "zero_probability_rows": zero}


def common_pair_holdout(records, minimum_count, seed):
    rows = sorted(((r.get("sex"), r["surname"], r["name"]) for r in records),
                  key=lambda row: (str(row[0]), row[1], row[2]))
    random.Random(seed).shuffle(rows)
    train_end, validation_end = int(.6 * len(rows)), int(.8 * len(rows))
    train, validation, test = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    models = fit_common_pairs(train, minimum_count)
    validation_scores = [(alpha, score_common_pairs(validation, models, alpha)) for alpha in ALPHAS]
    eligible = [(alpha, result) for alpha, result in validation_scores if result["mean_log_loss_bits"] is not None]
    if not eligible:
        return {"status": "no_finite_validation_score", "seed": seed, "minimum_train_pair_count": minimum_count}
    alpha, chosen = min(eligible, key=lambda item: (item[1]["mean_log_loss_bits"], item[0]))
    baseline, selected = score_common_pairs(test, models, 0), score_common_pairs(test, models, alpha)
    baseline_loss, selected_loss = baseline["mean_log_loss_bits"], selected["mean_log_loss_bits"]
    return {"seed": seed, "minimum_train_pair_count": minimum_count,
            "train_rows": len(train), "validation_rows": len(validation), "test_rows": len(test),
            "selected_cells_by_sex": {str(s): len(cells) for s, cells in models.items()},
            "validation_selected_alpha": alpha, "test_independent": baseline, "test_joint_mixture": selected,
            "test_joint_minus_independent_bits": selected_loss - baseline_loss
                if baseline_loss is not None and selected_loss is not None else None}


def resolution(n, population, threshold):
    p = threshold / population
    return {"scenario_population": population, "population_is_illustrative_not_verified_Russia_total": True,
            "candidate_threshold": threshold, "frequency_at_threshold": p,
            "expected_sample_occurrences_at_threshold": n * p,
            "zero_occurrence_probability_if_iid": math.exp(n * math.log1p(-p)),
            "people_per_one_sample_occurrence_if_representative": population / n,
            "ideal_relative_standard_error_at_threshold": math.sqrt((1 - p) / (n * p)),
            "ideal_sample_for_20pct_relative_standard_error": math.ceil((1 - p) / (.2 ** 2 * p)),
            "scope": "Illustration under independent representative binomial sampling; actual VK data do not satisfy this assumption"}


def load_frozen(directory, manifest_path):
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "plan": plan, "files": []}
        for path in sorted(directory.glob("page-*.json"), key=lambda p: int(p.stem[5:])):
            raw = path.read_bytes()
            if current_page(json.loads(raw)):
                manifest["files"].append({"name": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        write_private(manifest_path, manifest)
    planned = {f"page-{offset}.json" for offset in manifest["plan"]["offsets"]}
    records, current_ids, counts = {}, set(), Counter()
    files = sorted(manifest["files"], key=lambda item: (item["name"] not in planned, int(item["name"][5:-5])))
    for entry in files:
        if not re.fullmatch(r"page-[0-9]+\.json", entry["name"]):
            raise ValueError("Invalid manifest page name")
        raw = (directory / entry["name"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Frozen page hash mismatch")
        page = json.loads(raw)
        category = "current_plan" if entry["name"] in planned else "outside_plan"
        counts[category + "_pages"] += 1
        counts[category + "_returned_items"] += page["returned_items"]
        for row in page["records"]:
            records.setdefault(row["account_id"], row)
            if entry["name"] in planned:
                current_ids.add(row["account_id"])
    return manifest, list(records.values()), [row for uid, row in records.items() if uid in current_ids], dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-population", type=int, default=150000000)
    args = parser.parse_args()
    policy = json.loads(Path('configs/annotation-estimator-policy.json').read_text(encoding='utf-8'))
    threshold = policy["candidate_threshold"]
    if args.scenario_population <= threshold:
        parser.error("Scenario population must exceed the candidate threshold")
    directory = ROOT / "vk-groups" / "vk"
    manifest_path = directory / "annotation-evidence-manifest-2026-11-17.json"
    manifest, records, current, counts = load_frozen(directory, manifest_path)
    if len(current) < 100:
        raise ValueError("Insufficient frozen current-plan records")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "network_used": False,
              "manifest": str(manifest_path), "source_counts": counts, "full_FIO_available": False,
              "population_representativeness_confirmed": False, "current_plan": frequencies(current),
              "all_cached_pages": frequencies(records),
              "resolution_illustration": resolution(len(records), args.scenario_population, threshold),
              "resolution_at_one_million_accepted_accounts": resolution(1000000, args.scenario_population, threshold),
              "sex_conditional_lift": {}, "holdouts": {},
              "scope": "Account names in deterministic pages of one community, partial plan; no population or national alert calibration"}
    for sex in (1, 2):
        subset = [r for r in records if r.get("sex") == sex]
        report["sex_conditional_lift"][str(sex)] = observed_lift([(r["surname"], r["name"]) for r in subset])
    for label, subset in (("all_cached_pages", records), ("current_plan", current)):
        print(json.dumps({"stage": "holdout", "cohort": label, "accounts": len(subset)}), flush=True)
        report["holdouts"][label] = [common_pair_holdout(subset, minimum_count, seed)
                                     for minimum_count in (5, 20, 50) for seed in range(42, 47)]
    output = directory / "annotation-evidence-2026-11-17.json"
    write_private(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
