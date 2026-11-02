"""Validate draft contract examples, spans and negative schema cases; no HTTP calls."""

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def main():
    schema = json.loads(
        (ROOT / "docs/api/session-pii.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    examples = json.loads(
        (ROOT / "docs/api/session-pii-examples.json").read_text(encoding="utf-8")
    )

    def validate(name, value):
        Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}).validate(value)

    checks = 0
    for pair in examples["pairs"]:
        request, response = pair["request"], pair["response"]
        validate("Request", request)
        validate("Response", response)
        messages = request.get("messages", [request.get("message")])
        texts = {m["message_id"]: m["text"] for m in messages}
        assert response["session_id"] == request["session_id"]
        assert [r["message_id"] for r in response["results"]] == [
            m["message_id"] for m in messages
        ]
        for result in response["results"]:
            text = texts[result["message_id"]]
            for e in result["entities"]:
                assert 0 <= e["start"] < e["end"] <= len(text)
                if e["type"] == "PHONE":
                    assert text[e["start"] : e["end"]] == "+7 000 000-00-01"
                if e["type"] == "EMAIL":
                    assert text[e["start"] : e["end"]] == "lada@example.com"
                assert e["score_kind"] != "deterministic" or e["score"] is None
            verdict = result["session_verdict"]
            assert verdict["decision"] != "unknown" or not verdict["coverage_complete"]
            first = verdict["first_violation_message_id"]
            assert first is None or first <= result["message_id"]
            assert verdict["decision"] != "alert" or first is not None
            for person in verdict["persons"]:
                for marker in person["markers"]:
                    assert marker["message_id"] <= result["message_id"]
                    assert (
                        0
                        <= marker["start"]
                        < marker["end"]
                        <= len(texts[marker["message_id"]])
                    )
            if result["analysis_status"] == "not_in_scope":
                assert not result["entities"] and not verdict["coverage_complete"]
        checks += 1
    for error in examples["errors"]:
        validate("Error", error["response"])
        checks += 1
    request = examples["pairs"][0]["request"]
    bad_requests = [copy.deepcopy(request) for _ in range(3)]
    bad_requests[0]["messages"] = [request["message"]]
    bad_requests[1]["message"]["message_id"] = 0
    del bad_requests[2]["message"]
    bad_requests[2]["messages"] = []
    for invalid in bad_requests:
        assert not Draft202012Validator({**schema, "$ref": "#/$defs/Request"}).is_valid(
            invalid
        )
        checks += 1
    print(
        f"PASS: {checks} positive/negative example groups; Unicode spans and invariants checked"
    )
    print(
        "This validates documents, not runtime ordering, transactions, TTL or concurrency"
    )


if __name__ == "__main__":
    main()
