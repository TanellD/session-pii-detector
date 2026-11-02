"""Prepare pinned public weights and a self-contained local model, without texts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_manifest(directory, config):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for field in ("model", "revision", "backbone", "backbone_revision"):
        if manifest[field] != config[field]:
            raise ValueError(f"Prepared model differs from config: {field}")
    for name, digest in manifest["files"].items():
        if sha256(directory / name) != digest:
            raise ValueError(f"Prepared artifact hash mismatch: {name}")
    local_config = json.loads(
        (directory / "gliner_config.json").read_text(encoding="utf-8")
    )
    if Path(local_config["model_name"]).resolve() != (directory / "backbone").resolve():
        raise ValueError(
            "Prepared backbone must resolve locally; prepare again after moving it"
        )
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path("benchmarks/pii_smoke/config.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/task12-prepared-model")
    )
    args = parser.parse_args()
    # Public API for executable scripts; TLS verification stays enabled.
    if os.name == "nt":
        import truststore

        truststore.inject_into_ssl()
    from huggingface_hub import snapshot_download

    config = json.loads(args.config.read_text(encoding="utf-8"))
    model = Path(
        snapshot_download(
            config["model"],
            revision=config["revision"],
            token=False,
            allow_patterns=["gliner_config.json", "model.safetensors"],
            max_workers=2,
        )
    )
    backbone = Path(
        snapshot_download(
            config["backbone"],
            revision=config["backbone_revision"],
            token=False,
            allow_patterns=[
                "config.json",
                "tokenizer*",
                "special_tokens_map.json",
                "spm.model",
            ],
            max_workers=2,
        )
    )
    output = args.output.resolve()
    (output / "backbone").mkdir(parents=True, exist_ok=True)
    for source in backbone.iterdir():
        if source.is_file():
            shutil.copyfile(source, output / "backbone" / source.name)
    weights = output / "model.safetensors"
    if not weights.exists():
        try:
            os.link(model / weights.name, weights)
        except OSError:
            shutil.copyfile(model / weights.name, weights)
    elif sha256(weights) != sha256(model / weights.name):
        raise ValueError("Existing weights differ; choose a new --output directory")
    local_config = json.loads(
        (model / "gliner_config.json").read_text(encoding="utf-8")
    )
    local_config["model_name"] = str(output / "backbone")
    (output / "gliner_config.json").write_text(
        json.dumps(local_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        key: config[key]
        for key in ("model", "revision", "backbone", "backbone_revision")
    }
    manifest["original_gliner_config_sha256"] = sha256(model / "gliner_config.json")
    manifest["files"] = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    read_manifest(output, config)
    print(f"Pinned model and backbone prepared locally: {output}")


if __name__ == "__main__":
    main()
