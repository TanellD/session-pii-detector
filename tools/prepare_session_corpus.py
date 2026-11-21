"""Build and verify the local #33 corpus and the separate #40 alert ledger."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from annotation_calculator import Calculator
from annotation_contract import VERSION, validate
from session_alert_review import (
    GENERIC_PERSON,
    REVIEW_POLICY,
    REVIEW_SCHEMA,
    alert_steps,
    uses_review_policy,
    validate_review_document,
)


ROOT = Path(__file__).resolve().parents[1]
BASE = "022bf6290c8107149200efcc436c782da7c2995f"
POLICY_PATH = ROOT / "configs/annotation-estimator-policy.json"
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
SPLITS = {"development", "test"}
REASONS = {"red_rule", "combination_rule", "candidate_count_rule"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj):
    content = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(
                f"{path} differs from the materialized version. Use a new version directory."
            )
        return
    with path.open("xb") as stream:
        stream.write(content)


def unique_index(rows, field):
    indexed = {}
    for row in rows:
        key = row[field]
        if key in indexed:
            raise ValueError(f"Duplicate {field}: {key}")
        indexed[key] = row
    return indexed


def compile_corpus(plan, authored):
    reviewed = uses_review_policy(plan)
    scenarios = unique_index(plan["scenarios"], "id")
    dialogues = unique_index(authored, "id")
    if scenarios.keys() != dialogues.keys():
        raise ValueError("Scenario and dialogue ID sets differ")
    split_membership = {}
    documents, manifest = [], []
    for session_id, scenario in scenarios.items():
        split = scenario["split"]
        if split not in SPLITS:
            raise ValueError(f"Unknown split: {split}")
        for unit in [
            ("family", scenario["family"]),
            *(("profile", p) for p in scenario["profiles"]),
        ]:
            if split_membership.setdefault(unit, split) != split:
                raise ValueError(f"Scenario family or profile crosses splits: {unit}")
        messages, entities, shared_owners, known_owners = [], [], {}, set()
        for index, (row, step) in enumerate(
            zip(dialogues[session_id]["messages"], scenario["steps"], strict=True), 1
        ):
            text, *mentions = row
            message_id = f"m{index}"
            messages.append({"id": message_id, "text": text})
            actual = []
            for position, (kind, value, owner) in enumerate(mentions, 1):
                if not value or text.count(value) != 1:
                    raise ValueError(
                        f"{session_id}/{message_id}: expected a unique literal span"
                    )
                start = text.index(value)
                entity_id = f"{message_id}-e{position}"
                if isinstance(owner, list):
                    if (
                        len(owner) < 2
                        or len(set(owner)) != len(owner)
                        or not set(owner) <= known_owners
                    ):
                        raise ValueError(
                            f"{session_id}/{entity_id}: shared owners lack prior evidence"
                        )
                    if not reviewed:
                        shared_owners[entity_id] = owner
                        owner = None
                    owner_label = "shared"
                else:
                    owner_label = owner or "unknown"
                    if owner is not None:
                        known_owners.add(owner)
                actual.append(f"{owner_label}:{kind}")
                entities.append(
                    {
                        "id": entity_id,
                        "message_id": message_id,
                        "start": start,
                        "end": start + len(value),
                        "type": kind,
                        "person_id": owner,
                    }
                )
            if actual != step:
                raise ValueError(
                    f"{session_id}/{message_id}: spans or owners differ from plan"
                )
        document = {
            "schema_version": REVIEW_SCHEMA if reviewed else VERSION,
            "session_id": session_id,
            "messages": messages,
            "entities": entities,
            "case_flags": {
                "is_example": False,
                "annotation_uncertain": False,
                "clarification_reason": None,
            },
        }
        (validate_review_document if reviewed else validate)(document)
        documents.append(document)
        manifest.append(
            {
                "session_id": session_id,
                "split": split,
                "family": scenario["family"],
                "profiles": scenario["profiles"],
                "message_roles": {m["id"]: "user" for m in messages},
                **({} if reviewed else {"shared_owners": shared_owners}),
            }
        )
    return documents, manifest


def compile_gold(documents, ledger, calculator, *, reviewed=False):
    by_id = unique_index(ledger, "session_id")
    if by_id.keys() != {d["session_id"] for d in documents}:
        raise ValueError("Alert ledger must cover exactly the corpus")
    rows, differences = [], []
    for document in documents:
        session_id = document["session_id"]
        entities = defaultdict(list)
        for entity in document["entities"]:
            entities[entity["message_id"]].append(entity)
        available, active_by_owner = {}, {}
        steps, first = [], None
        predicted = alert_steps(document, calculator, reviewed=reviewed)
        for index, (message, entry, calculated) in enumerate(
            zip(
                document["messages"],
                by_id[session_id]["steps"],
                predicted,
                strict=True,
            ),
            1,
        ):
            message_id = message["id"]
            for entity in sorted(entities[message_id], key=lambda row: row["start"]):
                entity_id = entity["id"]
                available[entity_id] = entity
                owner = entity["person_id"]
                if isinstance(owner, str):
                    active_by_owner[owner, entity["type"]] = entity_id
            reason, evidence, uncertainty, rationale = entry
            if not rationale or (reason and (reason not in REASONS or not evidence)):
                raise ValueError(
                    f"Missing rationale or invalid rule: {session_id}/{message_id}"
                )
            evidence_ids = set(evidence)
            if (
                (not reason and evidence)
                or len(evidence_ids) != len(evidence)
                or not evidence_ids <= available.keys()
            ):
                raise ValueError(
                    f"Invalid or future evidence: {session_id}/{message_id}"
                )
            if reason:
                for entity_id in evidence:
                    entity = available[entity_id]
                    owner = entity["person_id"]
                    if (
                        isinstance(owner, str)
                        and active_by_owner[owner, entity["type"]] != entity_id
                    ):
                        raise ValueError(
                            f"Cited span was superseded: {session_id}/{message_id}/{entity_id}"
                        )
                cited_prefix = {
                    **document,
                    "messages": document["messages"][:index],
                    "entities": [available[entity_id] for entity_id in evidence],
                }
                if (
                    alert_steps(cited_prefix, calculator, reviewed=reviewed)[-1][
                        "reason"
                    ]
                    != reason
                ):
                    raise ValueError(
                        f"Cited spans do not support the rule: {session_id}/{message_id}"
                    )
            if uncertainty not in {
                None,
                "insufficient_statistics",
                "specification_gap",
            } or (reason and uncertainty):
                raise ValueError(f"Invalid uncertainty: {session_id}/{message_id}")
            step = {
                "message_id": message_id,
                "index": index,
                "rule_id": reason,
                "evidence_ids": evidence,
                "uncertainty_reason": uncertainty,
                "rationale": rationale,
            }
            steps.append(step)
            if reason and first is None:
                first = step
            actual = (calculated["reason"], calculated["clarification_reason"])
            if (reason, uncertainty) != actual:
                differences.append(
                    {
                        "session_id": session_id,
                        "message_id": message_id,
                        "author_rule": reason,
                        "calculator_rule": actual[0],
                        "author_uncertainty": uncertainty,
                        "calculator_uncertainty": actual[1],
                    }
                )
        rows.append(
            {
                "session_id": session_id,
                "status": "positive" if first else "negative",
                "t_gold": first["index"] if first else None,
                "steps": steps,
            }
        )
    return rows, differences


def write_review_pack(path, documents, test_ids):
    lines = [
        "# Независимая проверка первого алерта",
        "",
        "Версии схемы и политики указаны в карточке набора. Читайте реплики по порядку.",
        "Зафиксируйте решение после каждой реплики, прежде чем открыть следующую.",
        "Запишите спан, тип, владельца, правило, основание и причину уточнения.",
        "Первый алерт укажите по номеру сообщения. Ответ автора в пакете отсутствует.",
        "",
    ]
    for document in documents:
        if document["session_id"] not in test_ids:
            continue
        lines.extend((f"## {document['session_id']}", ""))
        for message in document["messages"]:
            lines.extend(
                (
                    f"<details><summary>{message['id']} · user</summary>",
                    "",
                    message["text"],
                    "",
                    "Решение проверяющего: ____________________",
                    "",
                    "</details>",
                    "",
                )
            )
    content = "\n".join(lines)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--with-alerts", action="store_true")
    args = parser.parse_args()
    directory = args.directory
    plan_path = directory / "scenario-plan.json"
    authored_path = directory / "authored-dialogues.json"
    plan = read(plan_path)
    reviewed = uses_review_policy(plan)
    review_metadata = {"review_policy": REVIEW_POLICY} if reviewed else {}
    documents, manifest = compile_corpus(plan, read(authored_path))
    sessions_path = directory / "sessions.json"
    write_json(sessions_path, documents)
    write_json(
        directory / "manifest.json",
        {
            "version": plan["version"],
            "schema": REVIEW_SCHEMA if reviewed else VERSION,
            "taxonomy": POLICY["taxonomy_version"],
            "spec_commit": BASE,
            **review_metadata,
            **({"extra_classes": {GENERIC_PERSON: "yellow"}} if reviewed else {}),
            "scenario_plan_sha256": digest(plan_path),
            "authored_dialogues_sha256": digest(authored_path),
            "sessions_sha256": digest(sessions_path),
            "sessions": manifest,
            "class_counts": dict(
                sorted(
                    Counter(
                        e["type"] for doc in documents for e in doc["entities"]
                    ).items()
                )
            ),
        },
    )
    print(
        f"Validated #33: {len(documents)} sessions, {sum(len(d['messages']) for d in documents)} messages"
    )
    if args.with_alerts:
        ledger_path = directory / "author-alert-ledger.json"
        gold, differences = compile_gold(
            documents,
            read(ledger_path),
            Calculator(policy_only=True),
            reviewed=reviewed,
        )
        write_json(
            directory / "gold.json",
            {
                "version": plan["version"],
                "sessions_sha256": digest(sessions_path),
                "policy": POLICY["version"],
                "policy_sha256": digest(POLICY_PATH),
                "author_ledger_sha256": digest(ledger_path),
                "review_status": "author_checked_pending_independent_review",
                "calculator_mode": "policy_only",
                **review_metadata,
                "calculator_disagreements": differences,
                "sessions": gold,
            },
        )
        write_review_pack(
            directory / "independent-review.md",
            documents,
            {row["session_id"] for row in manifest if row["split"] == "test"},
        )
        print(
            f"Checked #40: {len(gold)} author labels, {len(differences)} calculator disagreements"
        )
        if differences:
            raise SystemExit(
                "Review calculator-disagreements in gold.json before using the results"
            )


if __name__ == "__main__":
    main()
