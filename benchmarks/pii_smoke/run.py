"""Fixed-config CPU smoke experiment. Writes raw results only to ignored runs/."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time

from .evaluate import aggregate
from .regex_detector import merge, predict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/task12-smoke-v1.json"))
    parser.add_argument(
        "--config", type=Path, default=Path("benchmarks/pii_smoke/config.json")
    )
    parser.add_argument("--output", type=Path, default=Path("runs/task12-smoke"))
    parser.add_argument("--regex-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    data_bytes = args.data.read_bytes()
    cases = json.loads(data_bytes)["cases"]
    args.output.mkdir(parents=True, exist_ok=True)
    if config["repeats"] < 1:
        raise ValueError("repeats must be positive")
    import psutil

    meta = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "ram_gib": psutil.virtual_memory().total / 2**30,
        "available_ram_gib_at_start": psutil.virtual_memory().available / 2**30,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "worktree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "dataset_sha256": hashlib.sha256(data_bytes).hexdigest(),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "config": config,
        "device": "cpu",
        "regex_only": args.regex_only,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ["torch", "gliner", "transformers", "huggingface-hub", "psutil"]
        },
    }
    modes = {"regex": []}
    model = None
    if not args.regex_only:
        import torch
        from gliner import GLiNER

        torch.set_num_threads(config["threads"])
        torch.set_num_interop_threads(1)
        started = time.perf_counter()
        model = GLiNER.from_pretrained(
            config["model"],
            revision=config["revision"],
            cache_dir=os.environ.get("HF_HUB_CACHE"),
            load_tokenizer=True,
            local_files_only=True,
            token=False,
        )
        model.to("cpu").eval()
        meta["model_load_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        model.predict_entities(
            "Тестовая фраза.", list(config["labels"]), threshold=config["threshold"]
        )
        meta["warmup_seconds"] = time.perf_counter() - started
        modes.update({"gliner": [], "combined": []})
    for case in cases:
        rule_times, model_times, combined_times = [], [], []
        for _ in range(config["repeats"]):
            started = time.perf_counter()
            rules = predict(case["text"])
            rule_ms = (time.perf_counter() - started) * 1000
            rule_times.append(rule_ms)
            if model:
                started = time.perf_counter()
                found = model.predict_entities(
                    case["text"],
                    list(config["labels"]),
                    threshold=config["threshold"],
                    flat_ner=True,
                )
                model_ms = (time.perf_counter() - started) * 1000
                neural = [
                    {
                        "type": config["labels"][e["label"]],
                        "start": e["start"],
                        "end": e["end"],
                        "score": float(e["score"]),
                        "source": "gliner",
                    }
                    for e in found
                ]
                started = time.perf_counter()
                combined = merge(rules, neural)
                combined_ms = (
                    rule_ms + model_ms + (time.perf_counter() - started) * 1000
                )
                model_times.append(model_ms)
                combined_times.append(combined_ms)
        outputs = {"regex": (rules, rule_times)}
        if model:
            outputs.update(
                {
                    "gliner": (neural, model_times),
                    "combined": (combined, combined_times),
                }
            )
        for mode, (entities, durations) in outputs.items():
            for entity in entities:
                assert 0 <= entity["start"] < entity["end"] <= len(case["text"])
            modes[mode].append({**case, "predicted": entities, "latency_ms": durations})
        print(case["id"], "done", flush=True)
    summary = {"metadata": meta, "case_count": len(cases), "modes": {}}
    for mode, rows in modes.items():
        latency = [value for row in rows for value in row["latency_ms"]]
        # Nearest-rank p95, including all warmed per-message repetitions.
        p95 = sorted(latency)[max(0, __import__("math").ceil(0.95 * len(latency)) - 1)]
        summary["modes"][mode] = {
            "exact": aggregate(rows),
            "overlap": aggregate(rows, overlap=True),
            "latency_ms": {
                "samples": len(latency),
                "median": statistics.median(latency),
                "p95": p95,
            },
        }
        (args.output / f"{mode}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
