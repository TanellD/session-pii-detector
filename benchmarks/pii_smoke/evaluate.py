"""Extraction diagnostics only; session metrics are defined in docs/kb/metrics.md."""

from collections import Counter
import math
import statistics


def latency_summary(rows):
    values = [value for row in rows for value in row["latency_ms"]]
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Expected finite, nonnegative latency samples")
    return {
        "samples": len(values),
        "median": statistics.median(values),
        "p95": sorted(values)[math.ceil(0.95 * len(values)) - 1],
    }


def counts(gold, predicted, overlap=False):
    # Maximum bipartite matching prevents duplicate predictions inflating recall.
    edges = {}
    for i, prediction in enumerate(predicted):
        edges[i] = [
            j
            for j, target in enumerate(gold)
            if prediction["type"] == target["type"]
            and (
                max(prediction["start"], target["start"])
                < min(prediction["end"], target["end"])
                if overlap
                else (prediction["start"], prediction["end"])
                == (target["start"], target["end"])
            )
        ]
    assigned = {}

    def match(i, seen):
        for j in edges[i]:
            if j not in seen:
                seen.add(j)
                if j not in assigned or match(assigned[j], seen):
                    assigned[j] = i
                    return True
        return False

    tp = sum(match(i, set()) for i in edges)
    return {"tp": tp, "fp": len(predicted) - tp, "fn": len(gold) - tp}


def aggregate(rows, overlap=False):
    total = Counter()
    by_type = {}
    for row in rows:
        total.update(counts(row["expected"], row["predicted"], overlap))
        for kind in {e["type"] for e in row["expected"] + row["predicted"]}:
            by_type.setdefault(kind, Counter()).update(
                counts(
                    [e for e in row["expected"] if e["type"] == kind],
                    [e for e in row["predicted"] if e["type"] == kind],
                    overlap,
                )
            )
    return {
        "total": dict(total),
        "by_type": {kind: dict(value) for kind, value in sorted(by_type.items())},
    }
