"""Lazy GLiNER loader; used by adapter_check --with-model and service wrappers."""

import json
from pathlib import Path

_model = None
_config = None
_labels = None


def get_model(
    config_path=Path("benchmarks/pii_smoke/config.json"),
    prepared_dir=Path("outputs/task12-prepared-model"),
    local_only=True,
):
    global _model, _config, _labels
    if _model is None:
        from gliner import GLiNER

        _config = json.loads(config_path.read_text(encoding="utf-8"))
        _labels = list(_config["labels"].keys())

        prepared = Path(prepared_dir)
        if local_only and (prepared / "manifest.json").exists():
            # Load from the pinned local copy, not HF cache.
            _model = GLiNER.from_pretrained(
                str(prepared),
                map_location="cpu",
                local_files_only=True,
            )
        else:
            # Fallback: HF cache (online if local_only=False).
            _model = GLiNER.from_pretrained(
                _config["model"],
                revision=_config["revision"],
                map_location="cpu",
                local_files_only=local_only,
            )
    return _model, _config, _labels


def extract_message(message: str, config_path=Path("benchmarks/pii_smoke/config.json")):
    from .adapter import extract

    model, config, labels = get_model(config_path)
    raw = model.predict_entities(message, labels, threshold=config["threshold"])
    gliner_entities = [
        {"label": e["label"], "start": e["start"], "end": e["end"], "score": e["score"]}
        for e in raw
    ]
    return extract(message, gliner_entities=gliner_entities, config=config)
