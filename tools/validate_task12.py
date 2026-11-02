"""Validate draft document examples, not HTTP runtime/transactions/concurrency."""

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "docs/api/session-pii.schema.json").read_text(encoding="utf-8")
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(name, value):
    Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{name}"}).validate(value)


def validate_verdict(verdict, message_id):
    first = verdict["first_violation_message_id"]
    require(first is None or first <= message_id, "First alert cannot be in the future")
    require(
        (verdict["decision"] == "alert") == (first is not None),
        "Historical alert is latched within epoch",
    )
    require(
        verdict["decision"] != "unknown" or not verdict["coverage_complete"],
        "Unknown requires incomplete coverage",
    )
    if not verdict["coverage_complete"]:
        require(
            bool(verdict["reason"] and verdict["reason"].strip()),
            "Incomplete coverage requires a reason",
        )
        require(
            verdict["decision"] in {"unknown", "alert"},
            "Incomplete coverage cannot imply no_alert/warning",
        )


def validate_snapshot(state):
    validate("SessionSnapshot", state)
    require(
        state["next_message_id"] == state["state_version"] + 1,
        "Snapshot next ID/version mismatch",
    )
    validate_verdict(state, state["state_version"])


def validate_pair(pair):
    request, response = pair["request"], pair["response"]
    validate("Request", request)
    validate("Response", response)
    messages = request.get("messages", [request.get("message")])
    expected = request["expected_state_version"]
    require(
        [m["message_id"] for m in messages]
        == list(range(expected + 1, expected + len(messages) + 1)),
        "Messages must continue from expected version without gaps",
    )
    require(
        response["state_version"] == expected + len(messages),
        "Response version mismatch",
    )
    require(response["session_id"] == request["session_id"], "Session mismatch")
    require(response["session_epoch"] == request["session_epoch"], "Epoch mismatch")
    require(
        [r["message_id"] for r in response["results"]]
        == [m["message_id"] for m in messages],
        "Result IDs/order mismatch",
    )
    prior = pair.get("prior_messages", [])
    texts, available = {}, {}
    for item in prior:
        message = item["message"]
        validate("Message", message)
        require(
            message["message_id"] <= expected and message["message_id"] not in texts,
            "Invalid history fixture",
        )
        texts[message["message_id"]] = message["text"]
        available[message["message_id"]] = item["entities"]
    previous = pair.get("prior_verdict")
    if previous is not None:
        validate("SessionVerdict", previous)
        validate_verdict(previous, expected)
    for message, result in zip(messages, response["results"], strict=True):
        message_id, text = message["message_id"], message["text"]
        texts[message_id] = text
        available[message_id] = result["entities"]
        verdict = result["session_verdict"]
        validate_verdict(verdict, message_id)
        if previous is not None and previous["first_violation_message_id"] is not None:
            require(
                verdict["first_violation_message_id"]
                == previous["first_violation_message_id"],
                "First alert cannot be moved or cleared",
            )
        elif verdict["first_violation_message_id"] is not None:
            require(
                verdict["first_violation_message_id"] == message_id,
                "New first alert must be current message",
            )
        if previous is not None and not previous["coverage_complete"]:
            require(
                not verdict["coverage_complete"],
                "Coverage loss remains until reset/reanalysis",
            )
        person_ids = [person["person_id"] for person in verdict["persons"]]
        require(len(set(person_ids)) == len(person_ids), "Duplicate person IDs")
        require(
            result["message_verdict"]["has_signals"] == bool(result["entities"]),
            "has_signals must reflect entities",
        )
        for entity in result["entities"]:
            require(
                0 <= entity["start"] < entity["end"] <= len(text),
                "Invalid Unicode entity span",
            )
            require(
                entity["score_kind"] != "deterministic" or entity["score"] is None,
                "Deterministic rule has no model score",
            )
            require(
                entity["person_id"] is None or entity["person_id"] in person_ids,
                "Entity refers to a missing person",
            )
            if entity["person_id"] is None:
                require(
                    not verdict["coverage_complete"],
                    "Unresolved person requires incomplete coverage",
                )
        for person in verdict["persons"]:
            if person["decision"] == "alert":
                require(
                    verdict["decision"] == "alert",
                    "Person alert requires session alert",
                )
            for marker in person["markers"]:
                source_id = marker["message_id"]
                require(
                    source_id <= message_id and source_id in texts,
                    "Missing marker history fixture",
                )
                require(
                    0 <= marker["start"] < marker["end"] <= len(texts[source_id]),
                    "Invalid marker span",
                )
                require(
                    any(
                        all(
                            entity[key] == marker[key]
                            for key in ("type", "start", "end")
                        )
                        and entity["person_id"] == person["person_id"]
                        for entity in available[source_id]
                    ),
                    "Marker does not refer to an extracted entity of this person",
                )
        if result["analysis_status"] == "not_in_scope":
            require(
                not result["entities"] and not verdict["coverage_complete"],
                "Skipped message cannot claim full coverage",
            )
            require(
                result["message_verdict"]["decision"] == "unknown",
                "Skipped message verdict must be unknown",
            )
        require(
            (message["role"] == "user") == (result["analysis_status"] == "analyzed"),
            "Draft role policy mismatch",
        )
        previous = verdict


def semantic_negative_cases(pair):
    mutations = []
    invalid = copy.deepcopy(pair)
    invalid["response"]["results"][0]["session_verdict"].update(
        coverage_complete=False, reason=None
    )
    mutations.append(invalid)
    invalid = copy.deepcopy(pair)
    invalid["response"]["results"][0]["session_verdict"][
        "first_violation_message_id"
    ] = 1
    mutations.append(invalid)
    invalid = copy.deepcopy(pair)
    invalid["response"]["results"][0]["entities"][0]["person_id"] = "missing-person"
    mutations.append(invalid)
    invalid = copy.deepcopy(pair)
    invalid["response"]["results"][0]["message_verdict"]["has_signals"] = False
    mutations.append(invalid)
    return mutations


def main():
    Draft202012Validator.check_schema(SCHEMA)
    examples = json.loads(
        (ROOT / "docs/api/session-pii-examples.json").read_text(encoding="utf-8")
    )
    checks = 0
    pairs = {pair["name"]: pair for pair in examples["pairs"]}
    for pair in pairs.values():
        validate_pair(pair)
        if "retry_of" in pair:
            original = pairs[pair["retry_of"]]
            require(
                pair["request"] == original["request"]
                and pair["response"] == original["response"],
                "Retry must replay exact request/response",
            )
        checks += 1
    for item in examples["lifecycle"]:
        validate_snapshot(item["response"])
        if item["name"] == "create":
            validate("CreateRequest", item["request"])
            require(
                item["request"]["session_id"] == item["response"]["session_id"]
                and item["response"]["state_version"] == 0,
                "Invalid bootstrap",
            )
        if item["name"] == "reset":
            validate("ResetRequest", item["request"])
            before, after = item["before"], item["response"]
            validate_snapshot(before)
            require(
                item["request"]["session_epoch"] == before["session_epoch"]
                and item["request"]["expected_state_version"]
                == before["state_version"],
                "Reset precondition mismatch",
            )
            require(
                before["session_id"] == after["session_id"]
                and before["session_epoch"] != after["session_epoch"]
                and after["state_version"] == 0
                and after["first_violation_message_id"] is None,
                "Reset must start a fresh epoch",
            )
        checks += 1
    for error in examples["errors"]:
        response = error["response"]
        validate("Error", response)
        if error["http_status"] == 409:
            require(response["state"] is not None, "Conflict needs recovery metadata")
        if response["state"] is not None:
            validate_snapshot(response["state"])
        if "conflicts_with" in error:
            original = pairs[error["conflicts_with"]]["request"]
            validate("Request", error["request"])
            require(
                error["request"] != original
                and error["request"]["message"]["message_id"]
                == original["message"]["message_id"],
                "Changed-payload fixture must conflict on the same ID",
            )
        checks += 1
    request = pairs["single_unicode"]["request"]
    bad_requests = [copy.deepcopy(request) for _ in range(4)]
    bad_requests[0]["messages"] = [request["message"]]
    bad_requests[1]["message"]["message_id"] = 0
    del bad_requests[2]["message"]
    bad_requests[2]["messages"] = []
    del bad_requests[3]["session_epoch"]
    for invalid in bad_requests:
        try:
            validate("Request", invalid)
        except ValidationError:
            checks += 1
        else:
            raise ValueError("Invalid request unexpectedly passed")
    for invalid in semantic_negative_cases(pairs["single_unicode"]):
        try:
            validate_pair(invalid)
        except ValueError:
            checks += 1
        else:
            raise ValueError("Contradictory response unexpectedly passed")
    # Explicitly test code-point semantics in the fixture containing an emoji.
    emoji = pairs["single_unicode"]
    entity = emoji["response"]["results"][0]["entities"][0]
    require(
        emoji["request"]["message"]["text"][entity["start"] : entity["end"]]
        == "+7 000 000-00-01",
        "Unicode slicing mismatch",
    )
    print(
        f"PASS: {checks} positive/negative example groups; Unicode spans and semantic invariants checked"
    )
    print(
        "Documents only: runtime ordering, transactions, TTL and concurrency are not implemented"
    )


if __name__ == "__main__":
    main()
