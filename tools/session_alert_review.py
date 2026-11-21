"""Local corpus rules for the review of PR #46."""

from pathlib import Path
import sys

from annotation_contract import assess, validate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.pii_smoke.regex_detector import valid_inn, valid_luhn, valid_snils


CORPUS_VERSION = "session-alerts-v2"
REVIEW_POLICY = "pr46-review-v1"
GENERIC_PERSON = "person"
DOCUMENT_CHECKSUMS = {
    "individual_inn": valid_inn,
    "snils": valid_snils,
    "bank_card": valid_luhn,
}


def validate_review_document(document):
    """Reuse the base span validator for the v2 owner and person extensions."""
    projected = []
    for entity in document["entities"]:
        owners = entity["person_id"]
        if isinstance(owners, list):
            if (
                len(owners) < 2
                or not all(isinstance(owner, str) and owner.strip() for owner in owners)
                or len(set(owners)) != len(owners)
            ):
                raise ValueError("Expected at least two distinct shared owners")
            entity = {**entity, "person_id": None}
        if entity["type"] == GENERIC_PERSON:
            entity = {**entity, "type": "first_name"}
        projected.append(entity)
    return validate({**document, "entities": projected})


def review_steps(document, calculator):
    messages = validate_review_document(document)
    entities, person_messages = [], set()
    for entity in document["entities"]:
        kind = entity["type"]
        owner = entity["person_id"]
        if kind == GENERIC_PERSON:
            if isinstance(owner, str):
                person_messages.add(entity["message_id"])
            continue
        checksum = DOCUMENT_CHECKSUMS.get(kind)
        if checksum:
            value = messages[entity["message_id"]][entity["start"] : entity["end"]]
            if not checksum(value):
                continue
        # The pinned qualitative rules use personal contacts and addresses.
        # Shared ownership is kept in the corpus without copying it to people.
        entities.append(
            {**entity, "person_id": None} if isinstance(owner, list) else entity
        )
    steps = assess({**document, "entities": entities}, calculator)["messages"]
    person_seen = False
    for step in steps:
        person_seen |= step["message_id"] in person_messages
        if person_seen and not step["rule_alert"]:
            step.update(
                assessment="insufficient_data",
                annotation_uncertain=True,
                clarification_reason="insufficient_statistics",
            )
    return steps


def alert_steps(document, calculator, *, reviewed=False):
    if reviewed:
        return review_steps(document, calculator)
    return assess(document, calculator)["messages"]
