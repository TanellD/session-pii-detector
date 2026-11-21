"""Local corpus rules for the review of PR #46."""

from pathlib import Path
import sys

from annotation_calculator import normalize_state
from annotation_contract import VERSION, assess, validate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.pii_smoke.regex_detector import valid_inn, valid_luhn, valid_snils


REVIEW_POLICY = "pr46-review-v1"
REVIEW_SCHEMA = "annotation-mvp-v2-review46"
GENERIC_PERSON = "person"
DOCUMENT_CHECKSUMS = {
    "individual_inn": valid_inn,
    "snils": valid_snils,
    "bank_card": valid_luhn,
}


def uses_review_policy(metadata):
    policy = metadata.get("review_policy")
    if policy not in (None, REVIEW_POLICY):
        raise ValueError(f"Unsupported review policy: {policy}")
    return policy == REVIEW_POLICY


def validate_review_document(document):
    """Reuse the base span validator for the v2 owner and person extensions."""
    if document.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError(f"Expected schema {REVIEW_SCHEMA}")
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
    return validate({**document, "schema_version": VERSION, "entities": projected})


class ChecksumCalculator:
    def __init__(self, calculator, invalid_documents):
        self.calculator = calculator
        self.invalid_documents = invalid_documents
        self.policy = calculator.policy
        self.geo = calculator.geo

    def person(self, facts):
        documents = normalize_state(
            [fact for fact in facts if fact["type"] in DOCUMENT_CHECKSUMS]
        )
        # Apply checksum eligibility after replacement of the active document.
        # An invalid correction must supersede a previously valid value.
        eligible = [
            fact
            for fact in documents
            if fact["entity_id"] not in self.invalid_documents
        ]
        other_facts = [fact for fact in facts if fact["type"] not in DOCUMENT_CHECKSUMS]
        return self.calculator.person(other_facts + eligible)


def review_steps(document, calculator):
    messages = validate_review_document(document)
    entities, person_messages, invalid_documents = [], set(), set()
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
                if not isinstance(owner, str):
                    continue
                invalid_documents.add(entity["id"])
        # The pinned qualitative rules use personal contacts and addresses.
        # Shared ownership is kept in the corpus without copying it to people.
        entities.append(
            {**entity, "person_id": None} if isinstance(owner, list) else entity
        )
    steps = assess(
        {**document, "schema_version": VERSION, "entities": entities},
        ChecksumCalculator(calculator, invalid_documents),
    )["messages"]
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
