"""Replay locally available #41 baselines using the pinned annotation policy."""

import argparse
from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from annotation_calculator import Calculator, RED
from annotation_contract import assess, validate
from evaluate_session_alerts import evaluate
from prepare_session_corpus import (
    BASE,
    POLICY,
    POLICY_PATH,
    ROOT,
    digest,
    read,
    unique_index,
    write_json,
)

sys.path.insert(0, str(ROOT))

from benchmarks.pii_smoke.evaluate import counts  # noqa: E402
from benchmarks.pii_smoke.prepare import read_manifest  # noqa: E402
from benchmarks.pii_smoke.regex_detector import predict  # noqa: E402


STRATEGIES = ("message_only", "pooled_known_owners", "per_person")
CLASS_PATH = ROOT / "configs/annotation-classes.json"
SMOKE_CONFIG = ROOT / "benchmarks/pii_smoke/config.json"
SELF_CUES = {
    "phone": r"мой (?:личный )?(?:телефон|номер)",
    "email": r"мо[яю] (?:личн(?:ая|ую) )?(?:почт[ау]|email)",
    "address": r"(?:мой адрес(?: проживания)?|я живу по адресу)",
    "age": r"мне",
    "organization": r"мой работодатель",
    "occupation": r"я работаю",
    "geography": r"я живу в",
}
SEX_VALUES = {"женщина", "мужчина", "женский", "мужской"}
HOUSE = re.compile(r"(?:\bд\.|\bдом)\s*\d", re.I)
PHONE = re.compile(r"(?:\+7|8)[ ()\d-]+")


def load_corpus(directory):
    corpus_path = directory / "sessions.json"
    manifest, gold = read(directory / "manifest.json"), read(directory / "gold.json")
    if (
        digest(corpus_path) != manifest["sessions_sha256"]
        or manifest["sessions_sha256"] != gold["sessions_sha256"]
        or manifest["version"] != gold["version"]
        or manifest["spec_commit"] != BASE
        or manifest["taxonomy"] != POLICY["taxonomy_version"]
        or gold["calculator_disagreements"]
        or gold["policy_sha256"] != digest(POLICY_PATH)
        or gold["calculator_mode"] != "policy_only"
    ):
        raise ValueError("Corpus, gold or policy provenance is inconsistent")
    corpus = read(corpus_path)
    metadata = unique_index(manifest["sessions"], "session_id")
    if set(metadata) != set(unique_index(corpus, "session_id")):
        raise ValueError("Manifest and corpus sessions differ")
    for document in corpus:
        validate(document)
    return corpus, manifest, gold


def replay(document, strategy, calculator):
    if strategy == "message_only":
        steps = []
        for message in document["messages"]:
            isolated = {
                **document,
                "messages": [message],
                "entities": [
                    e for e in document["entities"] if e["message_id"] == message["id"]
                ],
            }
            steps.extend(assess(isolated, calculator)["messages"])
    else:
        if strategy == "pooled_known_owners":
            document = {
                **document,
                "entities": [
                    {**entity, "person_id": "pooled"}
                    if entity["person_id"] is not None
                    else entity
                    for entity in document["entities"]
                ],
            }
        elif strategy != "per_person":
            raise ValueError(f"Unsupported strategy: {strategy}")
        steps = assess(document, calculator)["messages"]
    first = next(
        ((i, s["reason"]) for i, s in enumerate(steps, 1) if s["rule_alert"]), None
    )
    return {
        "session_id": document["session_id"],
        "status": "complete",
        "t_pred": first[0] if first else None,
        "rule_id": first[1] if first else None,
        "uncertain_steps": [
            {"message_id": s["message_id"], "reason": s["clarification_reason"]}
            for s in steps
            if s["annotation_uncertain"]
        ],
    }


def owner_from_text(text, kind, start):
    """A narrow baseline using explicit self-attribution and no reference labels."""
    prefix = text[:start]
    if kind == "sex":
        return (
            "speaker"
            if re.search(r"(?:^|[.!?,:;]\s+)я\s*[—–-]?\s*$", prefix, re.IGNORECASE)
            else None
        )
    cue = SELF_CUES.get(kind)
    if cue and re.search(
        rf"(?:^|[.!?,:;]\s+)\s*(?:{cue})\b[^.!?,;\d@]{{0,32}}$",
        prefix,
        re.IGNORECASE,
    ):
        return "speaker"
    return None


def detector_priority(entity, mapping):
    kind = mapping.get(entity["type"])
    if entity["source"] == "regex":
        return (0 if kind in RED else 1, 0, entity["start"])
    return (2, -entity["score"], entity["start"])


def adapt_message(document, message, raw, mapping):
    text = message["text"]
    accepted, discarded = [], Counter()
    for prediction in sorted(raw, key=lambda e: detector_priority(e, mapping)):
        kind = mapping.get(prediction["type"])
        if not kind:
            discarded[f"unmapped:{prediction['type']}"] += 1
            continue
        start, end = prediction["start"], prediction["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
        ):
            discarded["invalid_span"] += 1
            continue
        value = text[start:end]
        if kind == "sex" and value.casefold() not in SEX_VALUES:
            discarded["unsupported_sex_value"] += 1
            continue
        if kind == "phone" and (
            not PHONE.fullmatch(value) or len(re.findall(r"\d", value)) != 11
        ):
            discarded["incomplete_phone"] += 1
            continue
        if kind == "address" and not HOUSE.search(value):
            discarded["address_without_house"] += 1
            continue
        if any(a["start"] < end and start < a["end"] for a in accepted):
            discarded["overlap"] += 1
            continue
        entity = {
            "id": f"{message['id']}-pred{len(accepted) + 1}",
            "message_id": message["id"],
            "start": start,
            "end": end,
            "type": kind,
            "person_id": owner_from_text(text, kind, start),
        }
        try:
            validate({**document, "messages": [message], "entities": [entity]})
        except ValueError:
            discarded["contract_validation"] += 1
            continue
        accepted.append(entity)
    return sorted(accepted, key=lambda entity: entity["start"]), discarded


def load_gliner(model_dir, config):
    # The model must already be prepared by the existing smoke benchmark.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from gliner import GLiNER

    torch.set_num_threads(config["threads"])
    torch.set_num_interop_threads(1)
    manifest = read_manifest(model_dir, config)
    model = GLiNER.from_pretrained(
        str(model_dir.resolve()),
        load_tokenizer=True,
        local_files_only=True,
        token=False,
        strict=True,
    )
    return model.to("cpu").eval(), manifest


def run_ner(corpus, config, model, mapping, groups):
    corpus_predictions, raw_messages = [], []
    discarded = Counter()
    extraction = {split: Counter() for split in set(groups.values())}
    for document in corpus:
        session_id = document["session_id"]
        # The adapter receives message text and structural metadata only.
        inference_input = {
            **document,
            "entities": [],
            "case_flags": {
                "is_example": False,
                "annotation_uncertain": False,
                "clarification_reason": None,
            },
        }
        entities = []
        for message in document["messages"]:
            text = message["text"]
            candidates = predict(text, require_valid_checksum=False)
            if model is not None:
                candidates.extend(
                    {
                        "type": config["labels"][e["label"]],
                        "start": e["start"],
                        "end": e["end"],
                        "score": float(e["score"]),
                        "source": "gliner",
                    }
                    for e in model.predict_entities(
                        text,
                        list(config["labels"]),
                        threshold=config["threshold"],
                        flat_ner=True,
                    )
                )
            accepted, rejected = adapt_message(
                inference_input, message, candidates, mapping
            )
            discarded.update(rejected)
            gold_mentions = [
                e for e in document["entities"] if e["message_id"] == message["id"]
            ]
            extraction[groups[session_id]].update(counts(gold_mentions, accepted))
            entities.extend(accepted)
            raw_messages.append(
                {
                    "session_id": session_id,
                    "message_id": message["id"],
                    "candidates": candidates,
                    "accepted": accepted,
                    "discarded": dict(rejected),
                }
            )
        predicted_document = {**inference_input, "entities": entities}
        validate(predicted_document)
        corpus_predictions.append(predicted_document)
    return corpus_predictions, {
        "extraction_by_split": {k: dict(v) for k, v in sorted(extraction.items())},
        "discarded": dict(sorted(discarded.items())),
        "messages": raw_messages,
    }


def code_version():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    changed = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    )
    return {"commit": head, "dirty": bool(changed.strip())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--gold-only", action="store_true")
    inputs.add_argument("--regex-only", action="store_true")
    inputs.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output directory")
    corpus, manifest, gold = load_corpus(args.corpus)
    groups = {r["session_id"]: r["split"] for r in manifest["sessions"]}
    calculator = Calculator(policy_only=True)
    modes = {"gold_spans_and_owners": corpus}
    config = read(SMOKE_CONFIG)
    taxonomy = read(CLASS_PATH)
    raw = None
    detector = None
    if not args.gold_only:
        mode = "regex" if args.regex_only else "regex_gliner"
        model, prepared = (
            (None, None) if args.regex_only else load_gliner(args.model_dir, config)
        )
        modes[mode], raw = run_ner(
            corpus, config, model, taxonomy["legacy_ner_mapping"], groups
        )
        detector = {
            "mode": mode,
            "owner_strategy": "explicit_self_current_message",
            "config_sha256": digest(SMOKE_CONFIG),
            "checksum_required": False,
            "gliner_version": version("gliner") if model is not None else None,
            "torch_version": version("torch") if model is not None else None,
            "prepared_model": prepared,
        }
    predictions = {
        "sessions_sha256": manifest["sessions_sha256"],
        "policy": POLICY["version"],
        "gold_sha256_for_report": digest(args.corpus / "gold.json"),
        "policy_sha256": digest(POLICY_PATH),
        "taxonomy_sha256": digest(CLASS_PATH),
        "policy_mode": "policy_only",
        "session_splits": groups,
        "implementation": code_version(),
        "spec_commit": BASE,
        "python": platform.python_version(),
        "device": "cpu",
        "detector": detector,
        "modes": {
            mode: {
                strategy: [
                    replay(document, strategy, calculator) for document in documents
                ]
                for strategy in STRATEGIES
            }
            for mode, documents in modes.items()
        },
    }
    result = evaluate(gold, predictions)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "predictions.json", predictions)
    write_json(args.output / "metrics.json", result)
    if raw is not None:
        write_json(args.output / "ner-diagnostics.json", raw)
    print(
        json.dumps(
            {"output": str(args.output), "modes": list(modes)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
