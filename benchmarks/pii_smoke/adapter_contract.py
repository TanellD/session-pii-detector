"""Mention contract for the regex/GLiNER adapter."""

from dataclasses import asdict, dataclass
import hashlib

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class Mention:
    id: str
    type: str
    text: str
    start: int
    end: int
    confidence: float | None
    confidence_kind: str
    source: str
    detector_version: str

    def to_dict(self):
        return asdict(self)


def make_mention(
    *,
    kind: str,
    text: str,
    start: int,
    end: int,
    confidence: float | None,
    confidence_kind: str,
    source: str,
    detector_version: str,
) -> Mention:
    if source not in {"regex", "gliner"}:
        raise ValueError(f"Unknown mention source: {source}")
    if confidence_kind not in {"model_score", "none"}:
        raise ValueError(f"Unknown confidence kind: {confidence_kind}")
    if not 0 <= start < end <= len(text):
        raise ValueError("Invalid mention span")

    identity = "\u001f".join(
        [CONTRACT_VERSION, kind, str(start), str(end), source, detector_version]
    )
    return Mention(
        id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        type=kind,
        text=text[start:end],
        start=start,
        end=end,
        confidence=confidence,
        confidence_kind=confidence_kind,
        source=source,
        detector_version=detector_version,
    )
