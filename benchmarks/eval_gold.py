"""Evaluate adapter against annotation-mvp-v2 gold sessions.

Not a quality benchmark for production; exact/type-aware/micro P/R/F2 only.
"""

import json
import math
from time import perf_counter_ns
from pathlib import Path

from benchmarks.adapter.adapter import extract
from benchmarks.adapter.adapter_rules import RULES_VERSION

# TODO(#PR): switch to configs/annotation-classes.json after merge.
# Source: configs/annotation-classes.json (PR, not in main yet).

ADAPTER_TO_GOLD = {
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "PATRONYMIC": "patronymic",
    "PHONE": "phone",
    "EMAIL": "email",
    "SERVICE_EMAIL": "email",
    "ADDRESS": "address",
    "CITY": "geography",
    "GENDER": "sex",
    "AGE": "age",
    "OCCUPATION": "occupation",
    "ORGANIZATION": "organization",
    "PASSPORT": "passport",
    "SNILS": "snils",
    "INN": "individual_inn",
    "CARD": "bank_card",
}
OUT_OF_SCOPE = {"DATE_OF_BIRTH", "RELATIONSHIP", "SPECIAL_CATEGORY"}
UNMAPPED = {"PERSON", "FULL_NAME"}


def load_gold(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gold_entities(session):
    """Return gold entities mapped to adapter output types with message_id."""
    mapped_types = set(ADAPTER_TO_GOLD.values())
    return [
        {
            "message_id": e["message_id"],
            "type": e["type"],
            "start": e["start"],
            "end": e["end"],
        }
        for e in session["entities"]
        if e["type"] in mapped_types
    ]


def adapter_entities(session, extract_fn, latency_samples=None, latency_repeats=0):
    """Run extract per message, return list with message_id."""
    result = []
    for message in session["messages"]:
        mentions = extract_fn(message["text"])
        if latency_samples is not None:
            for _ in range(latency_repeats):
                started = perf_counter_ns()
                extract_fn(message["text"])
                latency_samples.append((perf_counter_ns() - started) / 1_000_000)
        for m in mentions:
            gold_type = ADAPTER_TO_GOLD.get(m.type)
            if gold_type is None:
                continue  # out_of_scope or unmapped
            result.append(
                {
                    "message_id": message["id"],
                    "type": gold_type,
                    "start": m.start,
                    "end": m.end,
                }
            )
    return result


def match_entities(gold, pred):
    """Exact-match: same message_id, type, start, end."""
    gold_set = {(e["message_id"], e["type"], e["start"], e["end"]) for e in gold}
    pred_set = {(e["message_id"], e["type"], e["start"], e["end"]) for e in pred}
    tp = len(gold_set & pred_set)
    return {
        "tp": tp,
        "fp": len(pred_set) - tp,
        "fn": len(gold_set) - tp,
    }


def match_type_aware(gold, pred):
    """Type-aware: same message_id, start, end; type may differ."""
    gold_set = {(e["message_id"], e["start"], e["end"]) for e in gold}
    pred_set = {(e["message_id"], e["start"], e["end"]) for e in pred}
    tp = len(gold_set & pred_set)
    return {
        "tp": tp,
        "fp": len(pred_set) - tp,
        "fn": len(gold_set) - tp,
    }


def micro_prf2(total):
    tp, fp, fn = total["tp"], total["fp"], total["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f2 = (
        (5 * precision * recall / (4 * precision + recall))
        if precision + recall
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f2": f2}


def evaluate(sessions, extract_fn, split=None, latency_repeats=0):
    total_exact = {"tp": 0, "fp": 0, "fn": 0}
    total_type_aware = {"tp": 0, "fp": 0, "fn": 0}
    by_type = {}
    errors = []
    latency_samples = []

    for session in sessions:
        if split and session.get("split") != split:
            continue
        gold = gold_entities(session)
        pred = adapter_entities(
            session,
            extract_fn,
            latency_samples if latency_repeats else None,
            latency_repeats,
        )

        exact = match_entities(gold, pred)
        type_aware = match_type_aware(gold, pred)

        for k in total_exact:
            total_exact[k] += exact[k]
            total_type_aware[k] += type_aware[k]

        for kind in {e["type"] for e in gold + pred}:
            by_type.setdefault(
                kind,
                {
                    "exact": {"tp": 0, "fp": 0, "fn": 0},
                    "type_aware": {"tp": 0, "fp": 0, "fn": 0},
                },
            )
            g = [e for e in gold if e["type"] == kind]
            p = [e for e in pred if e["type"] == kind]
            c = match_entities(g, p)
            for k in c:
                by_type[kind]["exact"][k] += c[k]
            c = match_type_aware(g, p)
            for k in c:
                by_type[kind]["type_aware"][k] += c[k]

        if exact["fp"] or exact["fn"]:
            errors.append(
                {
                    "session_id": session["session_id"],
                    "gold": gold,
                    "pred": pred,
                    **exact,
                }
            )

    report = {
        "split": split or "all",
        "rules_version": RULES_VERSION,
        "exact": {"total": total_exact, **micro_prf2(total_exact), "by_type": by_type},
        "type_aware": {"total": total_type_aware, **micro_prf2(total_type_aware)},
        "errors": errors,
    }
    if latency_samples:
        ordered = sorted(latency_samples)
        report["adapter_latency_ms"] = {
            "scope": "per-message adapter extraction; model loading excluded",
            "sample_count": len(ordered),
            "repeats_per_message": latency_repeats,
            "percentile_method": "nearest-rank",
            "p50": ordered[math.ceil(0.50 * len(ordered)) - 1],
            "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
            "max": ordered[-1],
        }
    return report


def apply_splits(sessions, gold_path):
    """Attach development/test split from manifest to session records."""
    manifest_path = gold_path.parent / "manifest.json"
    if not manifest_path.exists():
        return sessions
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    split_by_id = {
        item["session_id"]: item["split"] for item in manifest.get("sessions", [])
    }
    for session in sessions:
        session["split"] = split_by_id.get(session["session_id"])
    return sessions


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument(
        "--split", choices=["development", "test", "all"], default="all"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runs/gold-eval/report.json")
    )
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--measure-latency", action="store_true")
    parser.add_argument("--latency-repeats", type=int, default=3)
    args = parser.parse_args()
    if args.measure_latency and args.latency_repeats < 1:
        parser.error("--latency-repeats must be at least 1")

    sessions = load_gold(args.gold)
    sessions = apply_splits(sessions, args.gold)
    if args.with_model:
        from benchmarks.adapter.adapter_gliner import extract_message

        threshold = args.threshold
        extract_fn = lambda text: extract_message(text, threshold=threshold)
    else:
        from benchmarks.adapter.adapter import extract as extract_fn

    split = None if args.split == "all" else args.split
    if args.measure_latency:
        selected_sessions = [
            session
            for session in sessions
            if split is None or session.get("split") == split
        ]
        first_message = next(
            (
                message["text"]
                for session in selected_sessions
                for message in session["messages"]
            ),
            None,
        )
        if first_message is not None:
            extract_fn(first_message)
    report = evaluate(
        sessions,
        extract_fn,
        split=split,
        latency_repeats=args.latency_repeats if args.measure_latency else 0,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    print(
        f"exact:  {report['exact']['total']}  P={report['exact']['precision']:.3f} R={report['exact']['recall']:.3f} F2={report['exact']['f2']:.3f}"
    )
    print(
        f"type:   {report['type_aware']['total']}  P={report['type_aware']['precision']:.3f} R={report['type_aware']['recall']:.3f} F2={report['type_aware']['f2']:.3f}"
    )
    if "adapter_latency_ms" in report:
        latency = report["adapter_latency_ms"]
        print(
            f"adapter latency (ms): n={latency['sample_count']} "
            f"p50={latency['p50']:.3f} p95={latency['p95']:.3f} "
            f"max={latency['max']:.3f}"
        )


if __name__ == "__main__":
    main()
