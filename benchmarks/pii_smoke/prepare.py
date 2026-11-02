"""Download public model artifacts; no example texts are sent to any service."""

import json
import os
from pathlib import Path


def main():
    # pip uses this same system-trust integration on current Windows Python.
    # Do not disable TLS verification for a corporate certificate chain.
    if os.name == "nt":
        from pip._vendor import truststore

        truststore.inject_into_ssl()
    from huggingface_hub import snapshot_download

    config = json.loads(
        Path("benchmarks/pii_smoke/config.json").read_text(encoding="utf-8")
    )
    snapshot_download(
        config["model"],
        revision=config["revision"],
        token=False,
        allow_patterns=["gliner_config.json", "model.safetensors"],
        max_workers=2,
    )
    # GLiNER's constructor requests the backbone by name without a revision.
    # Cache main for its offline lookup, then verify it is the audited snapshot.
    backbone = snapshot_download(
        config["backbone"],
        revision="main",
        token=False,
        allow_patterns=["config.json", "tokenizer*", "spm.model"],
        max_workers=2,
    )
    if Path(backbone).name != config["backbone_revision"]:
        raise RuntimeError(
            "Backbone main changed; audit the revision before running the experiment"
        )
    print("Public model and pinned backbone artifacts prepared")


if __name__ == "__main__":
    main()
