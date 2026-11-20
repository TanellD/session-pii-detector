"""Офлайн-проверка повторяемости ФИ и переноса совместной оценки на отложенные аккаунты."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import re
import time
from statistics import mean, median

from collect_vk_group_members import current_page, pair_summary
from research_name_pairs import ROOT, write_private


def holdout_pair(rows, strength, seed):
    rows = sorted(rows)
    random.Random(seed).shuffle(rows)
    split = int(len(rows) * .8)
    train, test = rows[:split], rows[split:]
    n = len(train)
    if not n or not test:
        raise ValueError("Insufficient rows")
    marginal = [Counter(row[i] for row in train) for i in range(2)]
    pairs = Counter(train)
    losses, counts = Counter(), Counter()
    for original in test:
        row = tuple(v if v in marginal[i] else None for i, v in enumerate(original))
        p = [(marginal[i][row[i]] + .5) / (n + .5 * (len(marginal[i]) + 1)) for i in range(2)]
        independent = p[0] * p[1]
        joint = (pairs[row] + strength * independent) / (n + strength)
        losses["independent"] -= math.log2(independent)
        losses["joint_shrunk"] -= math.log2(joint)
        counts["pair_seen_in_train"] += bool(pairs[row])
        counts["any_component_unseen_in_train"] += any(v is None for v in row)
    independent, joint = losses["independent"] / len(test), losses["joint_shrunk"] / len(test)
    return {"train_rows": n, "test_rows": len(test), "seed": seed, "strength": strength,
            "independent_log_loss_bits": independent, "joint_log_loss_bits": joint,
            "joint_minus_independent_bits": joint - independent,
            "pair_seen_in_train": counts["pair_seen_in_train"],
            "pair_unseen_in_train": len(test) - counts["pair_seen_in_train"],
            "any_component_unseen_in_train": counts["any_component_unseen_in_train"]}


def observed_lift(rows):
    """Descriptive joint/product ratios; selected cells are not population estimates."""
    n = len(rows)
    pairs = Counter(rows)
    first, second = Counter(r[0] for r in rows), Counter(r[1] for r in rows)
    result = []
    for threshold in (5, 10, 20):
        values = sorted(c * n / (first[f] * second[i]) for (f, i), c in pairs.items() if c >= threshold)
        result.append({"minimum_pair_count": threshold, "cells": len(values),
                       "median_joint_over_product": median(values) if values else None,
                       "minimum_joint_over_product": min(values) if values else None,
                       "maximum_joint_over_product": max(values) if values else None})
    return {"formula": "count(F,I)*n/(count(F)*count(I))", "thresholds": result,
            "scope": "In-sample descriptive ratios with count selection bias; not validated correction factors"}


def assess(records):
    rows = [(r["surname"], r["name"]) for r in records]
    if len(rows) < 10:
        return {"status": "insufficient_records"}
    cells = Counter(rows)
    report = {"accounts": len(rows), "distinct_pairs": len(cells),
              "singleton_pair_cells": sum(c == 1 for c in cells.values()),
              "repeated_pair_cells": sum(c > 1 for c in cells.values()),
              "accounts_in_repeated_pairs": sum(c for c in cells.values() if c > 1),
              "pair_cells_count_ge_5": sum(c >= 5 for c in cells.values()), "max_pair_count": max(cells.values()),
              "independent_population_validation": False, "full_patronymics_available": False}
    report["holdout"] = [holdout_pair(rows, strength, seed) for strength in (100, 1000, 10000, 100000)
                         for seed in (42, 43, 44, 45, 46)]
    report["holdout_summary"] = []
    for strength in (100, 1000, 10000, 100000):
        subset = [r for r in report["holdout"] if r["strength"] == strength]
        report["holdout_summary"].append({"strength": strength,
            "mean_independent_log_loss_bits": mean(r["independent_log_loss_bits"] for r in subset),
            "mean_joint_log_loss_bits": mean(r["joint_log_loss_bits"] for r in subset),
            "mean_joint_minus_independent_bits": mean(r["joint_minus_independent_bits"] for r in subset),
            "joint_wins_splits": sum(r["joint_minus_independent_bits"] < 0 for r in subset),
            "splits": len(subset)})
    report["by_declared_sex"] = {}
    report["observed_lift"] = observed_lift(rows)
    report["observed_lift_by_declared_sex"] = {}
    for sex in (1, 2):
        subset = [r for r in records if r.get("sex") == sex]
        if len(subset) >= 10:
            report["by_declared_sex"][str(sex)] = pair_summary(subset)
            report["observed_lift_by_declared_sex"][str(sex)] = observed_lift(
                [(r["surname"], r["name"]) for r in subset])
    shuffled = sorted(rows)
    random.Random(42).shuffle(shuffled)
    report["size_curve"] = []
    for size in sorted({min(len(rows), n) for n in (1000, 3000, len(rows))}):
        subset = shuffled[:size]
        report["size_curve"].append({"rows": size, "distinct_pairs": len(set(subset)),
                                      "holdout": holdout_pair(subset, 10000, 42)})
    report["scope"] = ("Exploratory account-row holdout within one community. add-0.5 marginals, "
                       "train-vocabulary UNK, joint frequencies shrunk to independent baseline. "
                       "Loss is for projected categories, not probability of an unseen literal name. "
                       "No tuned model, confidence guarantee or population correction coefficient.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="vk")
    parser.add_argument("--snapshot-limit", type=int, help="Analyze a preserved earlier plan without network")
    parser.add_argument("--wait-for-limit", type=int, help="Wait for the collector to complete this limit before analysis")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", args.group):
        parser.error("Use a community screen name or numeric ID")
    directory = ROOT / "vk-groups" / args.group
    if args.wait_for_limit is not None:
        if args.wait_for_limit < 1 or args.snapshot_limit is not None:
            parser.error("wait limit must be positive and cannot be combined with a snapshot")
        deadline = time.monotonic() + 10800
        print(json.dumps({"stage": "waiting_for_collection", "limit": args.wait_for_limit}), flush=True)
        while True:
            plan_path, result_path = directory / "plan.json", directory / "results.json"
            if plan_path.exists() and result_path.exists():
                pending_plan = json.loads(plan_path.read_text(encoding="utf-8"))
                collected = json.loads(result_path.read_text(encoding="utf-8"))
                if (pending_plan["limit"] == args.wait_for_limit and collected.get("complete_plan")
                        and collected.get("cached_pages") == len(pending_plan["offsets"])
                        and len(collected.get("page_sha256", [])) == len(pending_plan["offsets"])):
                    break
            if time.monotonic() >= deadline:
                raise TimeoutError("Collection did not finish within three hours; cached pages are preserved")
            time.sleep(10)
    output_directory = directory
    if args.snapshot_limit is not None:
        if args.snapshot_limit < 1:
            parser.error("snapshot limit must be positive")
        output_directory = directory / "snapshots" / f"limit-{args.snapshot_limit}"
    plan = json.loads((output_directory / "plan.json").read_text(encoding="utf-8"))
    records, seen, hashes, missing = [], set(), [], []
    for offset in plan["offsets"]:
        path = directory / f"page-{offset}.json"
        if not path.exists():
            missing.append(offset)
            continue
        raw = path.read_bytes()
        page = json.loads(raw)
        if not current_page(page):
            missing.append(offset)
            continue
        hashes.append(hashlib.sha256(raw).hexdigest())
        for row in page["records"]:
            if row["account_id"] not in seen:
                seen.add(row["account_id"])
                records.append(row)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "group": args.group,
              "network_used": False, "page_sha256": hashes, "missing_or_legacy_offsets": missing,
              **assess(records)}
    write_private(output_directory / "adequacy.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
