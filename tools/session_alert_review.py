"""Versioned qualitative corpus rules, independent of service weights."""
from pathlib import Path
import sys
from annotation_contract import VERSION, assess, validate
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.pii_smoke.regex_detector import valid_inn, valid_luhn, valid_snils

REVIEW_POLICY = "pr46-review-v2"
REVIEW_SCHEMA = VERSION
GENERIC_PERSON = "person"
DOCUMENT_CHECKSUMS = {"individual_inn": valid_inn, "snils": valid_snils, "bank_card": valid_luhn}

def uses_review_policy(metadata):
    policy = metadata.get("review_policy")
    if policy not in (None, REVIEW_POLICY):
        raise ValueError(f"Unsupported review policy: {policy}; migrate into a new corpus version")
    return policy == REVIEW_POLICY

def validate_review_document(document):
    if document.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError(f"Expected schema {REVIEW_SCHEMA}")
    messages = validate(document)
    for entity in document["entities"]:
        checksum = DOCUMENT_CHECKSUMS.get(entity["type"])
        if checksum and not checksum(messages[entity["message_id"]][entity["start"]:entity["end"]]):
            raise ValueError("Invalid document checksum is outside extraction gold")
    return messages

def review_steps(document, calculator):
    validate_review_document(document)
    return assess(document, calculator)["messages"]

def alert_steps(document, calculator, *, reviewed=False):
    return review_steps(document, calculator) if reviewed else assess(document, calculator)["messages"]
