"""Single-message regex + GLiNER adapter."""

import json
from pathlib import Path

from .adapter_contract import make_mention
from .adapter_rules import RULES_VERSION, apply_rules
from benchmarks.pii_smoke.regex_detector import predict


def _detector_version(config):
    if config:
        return f'{config.get("model", "gliner")}@{config.get("revision", "unknown")}'
    return RULES_VERSION


def extract(message: str, gliner_entities=None, config=None, detector_version=None):
    if not isinstance(message, str):
        raise TypeError("message must be a string")

    entities = predict(message)
    if gliner_entities:
        if config is None:
            config_path = Path("benchmarks/pii_smoke/config.json")
            config = json.loads(config_path.read_text(encoding="utf-8"))
        labels = config["labels"]
        for entity in gliner_entities:
            label = entity.get("label", entity.get("raw_label"))
            kind = labels.get(label)
            if kind is None:
                continue
            entities.append(
                {
                    "type": kind,
                    "start": int(entity["start"]),
                    "end": int(entity["end"]),
                    "score": float(entity["score"]),
                    "source": "gliner",
                }
            )

    entities = apply_rules(message, entities)
    gliner_version = detector_version or _detector_version(config)
    mentions = []
    for entity in entities:
        source = entity["source"]
        if not 0 <= entity["start"] < entity["end"] <= len(message):
            raise ValueError("Invalid adapted span")
        version = entity.get("detector_version") or (
            RULES_VERSION if source == "regex" else gliner_version
        )
        confidence_kind = (
            "none"
            if source == "regex" or entity.get("score") is None
            else "model_score"
        )
        confidence = None if confidence_kind == "none" else float(entity["score"])
        mentions.append(
            make_mention(
                kind=entity["type"],
                text=message,
                start=entity["start"],
                end=entity["end"],
                confidence=confidence,
                confidence_kind=confidence_kind,
                source=source,
                detector_version=version,
                attributes=entity.get("attributes"),
            )
        )
    return mentions
