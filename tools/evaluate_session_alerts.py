"""Score first alerts using the protocol in docs/kb/metrics.md."""

import argparse
from collections import Counter
import json
from math import ceil
from pathlib import Path
from statistics import median

from prepare_session_corpus import (
    POLICY,
    POLICY_PATH,
    SPLITS,
    digest,
    read,
    unique_index,
    write_json,
)


def ratio(numerator, denominator, missing_reason):
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "na_reason": None if denominator else missing_reason,
    }


def f2(counts):
    tp, fp, fn = (counts[key] for key in ("TP", "FP", "FN"))
    return ratio(5 * tp, 5 * tp + fp + 4 * fn, "no_tp_fp_or_fn")


def summarize_offsets(offsets):
    size = len(offsets)
    abs_offsets = sorted(map(abs, offsets))
    signs = Counter(offsets)
    return {
        "count": size,
        "signed_distribution": {str(k): v for k, v in sorted(signs.items())},
        "early_rate": ratio(
            sum(o < 0 for o in offsets), size, "no_session_true_positives"
        ),
        "exact_rate": ratio(signs[0], size, "no_session_true_positives"),
        "late_rate": ratio(
            sum(o > 0 for o in offsets), size, "no_session_true_positives"
        ),
        "median_absolute": median(abs_offsets) if size else None,
        "p95_absolute": abs_offsets[ceil(0.95 * size) - 1] if size else None,
        "max_absolute": abs_offsets[-1] if size else None,
        "quantile_method": "nearest_rank",
        "na_reason": None if size else "no_session_true_positives",
    }


def valid_index(value, length):
    return type(value) is int and 1 <= value <= length


def valid_reason_rows(rows, key):
    return isinstance(rows, list) and all(
        isinstance(row, dict)
        and key in row
        and (row[key] is None or isinstance(row[key], str))
        for row in rows
    )


def score(gold_rows, prediction_rows):
    gold = unique_index(gold_rows, "session_id")
    predictions = unique_index(prediction_rows, "session_id")
    unexpected = predictions.keys() - gold.keys()
    if unexpected:
        raise ValueError(f"Unrecognized predictions: {sorted(unexpected)}")
    session = dict.fromkeys(("TP", "FP", "FN", "TN"), 0)
    timely = dict.fromkeys(("TP", "FP", "FN", "TN"), 0)
    offsets, outcomes = [], []
    exclusions, uncertainty_reasons, prediction_reasons = (
        Counter(),
        Counter(),
        Counter(),
    )
    negative = Counter()
    gold_uncertain = prediction_uncertain = 0
    for session_id, row in gold.items():
        prediction = predictions.get(session_id)
        gold_index = row.get("t_gold")
        status = row.get("status")
        steps = row.get("steps")
        pred_index = prediction.get("t_pred") if prediction is not None else None
        uncertain_steps = (
            prediction.get("uncertain_steps", []) if prediction is not None else []
        )
        error = None
        if status == "unknown":
            error = "unresolved_gold"
        elif (
            not steps
            or not valid_reason_rows(steps, "uncertainty_reason")
            or "t_gold" not in row
            or (
                (status != "positive" or not valid_index(gold_index, len(steps)))
                and (status != "negative" or gold_index is not None)
            )
        ):
            error = "invalid_gold"
        elif prediction is None:
            error = "missing_prediction"
        elif prediction.get("status") != "complete":
            error = "incomplete_prediction"
        elif (
            "t_pred" not in prediction
            or (pred_index is not None and not valid_index(pred_index, len(steps)))
            or not valid_reason_rows(uncertain_steps, "reason")
        ):
            error = "invalid_prediction"
        if error:
            exclusions[error] += 1
            outcomes.append(
                {
                    "session_id": session_id,
                    "excluded": error,
                    "reason": row.get("unknown_reason")
                    if status == "unknown"
                    else None,
                }
            )
            continue
        reasons = {
            step["uncertainty_reason"] for step in steps if step["uncertainty_reason"]
        }
        predicted_reasons = {
            step["reason"] for step in uncertain_steps if step["reason"]
        }
        uncertainty_reasons.update(reasons)
        prediction_reasons.update(predicted_reasons)
        gold_uncertain += bool(reasons)
        prediction_uncertain += bool(predicted_reasons)

        offset = None
        if status == "positive":
            if pred_index is None:
                session_outcome, timely_outcome, case = "FN", "FN", "missed"
            else:
                session_outcome = "TP"
                offset = pred_index - gold_index
                offsets.append(offset)
                if offset < 0:
                    timely_outcome, case = "FP", "early"
                elif offset > 0:
                    timely_outcome, case = "FN", "late"
                else:
                    timely_outcome, case = "TP", "exact"
        else:
            session_outcome = timely_outcome = "FP" if pred_index is not None else "TN"
            case = "false_alert" if pred_index is not None else "correct_negative"
            negative["all"] += 1
            negative["fp_all"] += pred_index is not None
            if not reasons:
                negative["certain"] += 1
                negative["fp_certain"] += pred_index is not None
        session[session_outcome] += 1
        timely[timely_outcome] += 1
        outcomes.append(
            {
                "session_id": session_id,
                "t_gold": gold_index,
                "t_pred": pred_index,
                "offset": offset,
                "outcome": case,
                "prediction_rule": prediction.get("rule_id"),
            }
        )
    evaluated = sum(session.values())
    return {
        "f2_session": {"counts": session, **f2(session)},
        "f2_timely": {"counts": timely, **f2(timely)},
        "offset": summarize_offsets(offsets),
        "fp0_all_negative": ratio(
            negative["fp_all"], negative["all"], "no_negative_sessions"
        ),
        "fp0_certain_negative": ratio(
            negative["fp_certain"], negative["certain"], "no_certain_negative_sessions"
        ),
        "coverage": ratio(evaluated, len(gold), "no_input_sessions"),
        "uncertainty": {
            "gold_sessions": ratio(gold_uncertain, evaluated, "no_evaluable_sessions"),
            "prediction_sessions": ratio(
                prediction_uncertain, evaluated, "no_evaluable_sessions"
            ),
            "gold_reasons_by_session": dict(uncertainty_reasons),
            "prediction_reasons_by_session": dict(prediction_reasons),
        },
        "exclusions": dict(exclusions),
        "outcomes": outcomes,
    }


def evaluate(gold, predictions, groups=None):
    if (
        gold["sessions_sha256"] != predictions["sessions_sha256"]
        or gold["policy"] != predictions["policy"]
        or gold["policy"] != POLICY["version"]
        or gold["policy_sha256"] != predictions["policy_sha256"]
        or gold["policy_sha256"] != digest(POLICY_PATH)
    ):
        raise ValueError("Corpus or policy differs between gold and predictions")
    if groups is None and "session_splits" in predictions:
        groups = predictions["session_splits"]
    if groups is not None:
        if set(groups) != {r["session_id"] for r in gold["sessions"]}:
            raise ValueError("Split membership differs from gold")
        if not set(groups.values()) <= SPLITS:
            raise ValueError("Unrecognized split")
    modes = predictions["modes"]
    if not modes or any(not variants for variants in modes.values()):
        raise ValueError("Expected at least one evaluated mode and strategy")
    results = {}
    for mode, variants in modes.items():
        results[mode] = {}
        for strategy, rows in variants.items():
            if not rows:
                raise ValueError(f"No predictions for {mode}/{strategy}")
            aggregate = score(gold["sessions"], rows)  # Validate IDs before slicing.
            if groups is None:
                results[mode][strategy] = {"all": aggregate}
            else:
                results[mode][strategy] = {
                    split: score(
                        [
                            r
                            for r in gold["sessions"]
                            if groups[r["session_id"]] == split
                        ],
                        [r for r in rows if groups[r["session_id"]] == split],
                    )
                    for split in sorted(set(groups.values()))
                }
    return {
        "corpus_version": gold["version"],
        "sessions_sha256": gold["sessions_sha256"],
        "policy": gold["policy"],
        "policy_sha256": gold["policy_sha256"],
        "review_status": gold["review_status"],
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    predictions = read(args.predictions)
    if predictions.get("gold_sha256_for_report") not in {None, digest(args.gold)}:
        parser.error("Gold file differs from the one used for this report")
    result = evaluate(read(args.gold), predictions)
    write_json(args.output, result)
    print(
        json.dumps(
            {"output": str(args.output), "modes": list(result["results"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
