"""Fixed-config CPU smoke experiment. Writes raw results only to ignored runs/."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import time

from .evaluate import aggregate, latency_summary
from .prepare import read_manifest, sha256
from .regex_detector import merge, predict


def processor_name():
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    return platform.processor()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/task12-smoke-v2.json"))
    parser.add_argument(
        "--config", type=Path, default=Path("benchmarks/pii_smoke/config.json")
    )
    parser.add_argument("--output", type=Path, default=Path("runs/task12-smoke-v2"))
    parser.add_argument(
        "--model-dir", type=Path, default=Path("outputs/task12-prepared-model")
    )
    parser.add_argument("--regex-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    data_bytes = args.data.read_bytes()
    dataset = json.loads(data_bytes)
    cases = dataset["cases"]
    if not dataset["synthetic"] or not 20 <= len(cases) <= 30:
        raise ValueError("Expected 20–30 synthetic cases")
    if (args.output / "summary.json").exists():
        raise ValueError(
            "Run already exists; choose a new --output to preserve measurements"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    if config["repeats"] < 1:
        raise ValueError("repeats must be positive")
    import psutil

    meta = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset["version"],
        "timing_protocol": "v2: regex + model + adapter conversion + merge; no HTTP",
        "platform": platform.platform(),
        "processor": processor_name(),
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
            for name in (
                ["psutil"]
                if args.regex_only
                else [
                    "torch",
                    "gliner",
                    "transformers",
                    "huggingface-hub",
                    "psutil",
                    "truststore",
                ]
            )
        },
    }
    lock = Path("benchmarks/pii_smoke/requirements-win-py313.lock.txt")
    if lock.exists():
        meta["environment_lock_sha256"] = sha256(lock)
    (args.output / "freeze.txt").write_text(
        subprocess.check_output(
            [__import__("sys").executable, "-m", "pip", "freeze", "--all"], text=True
        ),
        encoding="utf-8",
    )
    modes = {"regex": []}
    model = None
    if not args.regex_only:
        import torch
        from gliner import GLiNER

        torch.set_num_threads(config["threads"])
        torch.set_num_interop_threads(1)
        meta["prepared_manifest"] = read_manifest(args.model_dir, config)
        started = time.perf_counter()
        model = GLiNER.from_pretrained(
            str(args.model_dir.resolve()),
            load_tokenizer=True,
            local_files_only=True,
            token=False,
            strict=True,
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
        repeat_predictions = []
        for _ in range(config["repeats"]):
            started = time.perf_counter()
            rules = predict(case["text"])
            rule_ms = (time.perf_counter() - started) * 1000
            rule_times.append(rule_ms)
            if model:
                started = time.perf_counter()
                adapter_started = started
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
                combined = merge(rules, neural)
                combined_ms = rule_ms + (time.perf_counter() - adapter_started) * 1000
                model_times.append(model_ms)
                combined_times.append(combined_ms)
                repeat_predictions.append(combined)
        if repeat_predictions and any(
            value != repeat_predictions[0] for value in repeat_predictions[1:]
        ):
            raise ValueError(f"Predictions changed between repetitions: {case['id']}")
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
                if not 0 <= entity["start"] < entity["end"] <= len(case["text"]):
                    raise ValueError(f"Invalid detector span: {case['id']}")
            modes[mode].append({**case, "predicted": entities, "latency_ms": durations})
        print(case["id"], "done", flush=True)
    summary = {"metadata": meta, "case_count": len(cases), "modes": {}}
    for mode, rows in modes.items():
        summary["modes"][mode] = {
            "exact": aggregate(rows),
            "overlap": aggregate(rows, overlap=True),
            "latency_ms": latency_summary(rows),
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
