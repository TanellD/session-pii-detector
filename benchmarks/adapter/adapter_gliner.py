"""Lazy GLiNER loader for benchmark adapter smoke."""

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
        try:
            from gliner import GLiNER
        except ImportError as exc:
            raise RuntimeError("GLiNER dependency is not installed") from exc

        _config = json.loads(config_path.read_text(encoding="utf-8"))
        _labels = list(_config["labels"])
        prepared = Path(prepared_dir)
        if local_only and (prepared / "manifest.json").exists():
            _model = GLiNER.from_pretrained(
                str(prepared), map_location="cpu", local_files_only=True
            )
        else:
            _model = GLiNER.from_pretrained(
                _config["model"],
                revision=_config["revision"],
                map_location="cpu",
                local_files_only=local_only,
            )
    if _labels is None:
        raise RuntimeError("GLiNER labels were not initialized")
    return _model, _config, _labels


def extract_message(
    message: str,
    config_path=Path("benchmarks/pii_smoke/config.json"),
    threshold: float | None = None,
):
    from .adapter import extract

    model, config, labels = get_model(config_path)
    selected_threshold = config["threshold"] if threshold is None else threshold
    raw = model.predict_entities(message, labels, threshold=selected_threshold)
    entities = [
        {
            "label": item["label"],
            "start": item["start"],
            "end": item["end"],
            "score": item["score"],
        }
        for item in raw
    ]
    return extract(message, gliner_entities=entities, config=config)
