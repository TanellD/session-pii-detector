"""Checks for diagnostic arithmetic, generated fixtures and result accounting."""

import copy
import hashlib
import json
import unittest

from benchmarks.pii_smoke.adapter import extract
from benchmarks.pii_smoke.adapter_contract import CONTRACT_VERSION
from benchmarks.pii_smoke.dataset import build
from benchmarks.pii_smoke.evaluate import counts
from benchmarks.pii_smoke.regex_detector import (
    merge,
    predict,
    valid_inn,
    valid_luhn,
    valid_snils,
)


class SmokeChecks(unittest.TestCase):
    def test_versioned_name_coverage_and_original_fixture(self):
        v2 = {row["id"]: row for row in build()}
        self.assertEqual(len(v2["full_name"]["text"].split()[2:]), 3)
        self.assertIn("patronymic", v2)
        original = (
            json.dumps(
                {"version": "task12-smoke-v1", "synthetic": True, "cases": build("v1")},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(original).hexdigest(),
            "613d64eb96a30886d1e2e62ba31ae654d1d4f092454e34b3aa263ff3435e0bab",
        )

    def test_fixture_spans_and_rules(self):
        rows = build()
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        for row in rows:
            for e in row["expected"]:
                self.assertTrue(0 <= e["start"] < e["end"] <= len(row["text"]))
            if row["id"] in {
                "phone",
                "email",
                "snils",
                "inn",
                "card",
                "passport",
                "code",
            }:
                self.assertEqual(
                    counts(row["expected"], predict(row["text"])),
                    {"tp": 1, "fp": 0, "fn": 0},
                )
            if row["id"] in {"bad_checksum", "ordinary_numbers", "negative"}:
                self.assertEqual(predict(row["text"]), [])

    def test_checksum_mutation(self):
        rows = {r["id"]: r for r in build()}
        for case_id, validator in [
            ("snils", valid_snils),
            ("inn", valid_inn),
            ("card", valid_luhn),
        ]:
            row = rows[case_id]
            e = row["expected"][0]
            value = row["text"][e["start"] : e["end"]]
            self.assertTrue(validator(value))
            self.assertFalse(validator(value[:-1] + str((int(value[-1]) + 1) % 10)))

    def test_duplicate_and_overlap_accounting(self):
        gold = [{"type": "PERSON", "start": 2, "end": 8}]
        self.assertEqual(counts(gold, gold * 2), {"tp": 1, "fp": 1, "fn": 0})
        partial = [{"type": "PERSON", "start": 4, "end": 8}]
        self.assertEqual(counts(gold, partial), {"tp": 0, "fp": 1, "fn": 1})
        self.assertEqual(
            counts(gold, partial, overlap=True), {"tp": 1, "fp": 0, "fn": 0}
        )
        wrong_type = [{"type": "CITY", "start": 2, "end": 8}]
        self.assertEqual(counts(gold, wrong_type, overlap=True)["tp"], 0)

    def test_global_matching_not_greedy(self):
        gold = [
            {"type": "PERSON", "start": 0, "end": 2},
            {"type": "PERSON", "start": 3, "end": 5},
        ]
        predictions = [
            {"type": "PERSON", "start": 0, "end": 5},
            {"type": "PERSON", "start": 0, "end": 2},
        ]
        self.assertEqual(
            counts(gold, predictions, overlap=True), {"tp": 2, "fp": 0, "fn": 0}
        )

    def test_merge_preserves_different_types_and_input(self):
        rules = [{"type": "EMAIL", "start": 0, "end": 10}]
        before = copy.deepcopy(rules)
        neural = [
            {"type": "EMAIL", "start": 1, "end": 10},
            {"type": "PERSON", "start": 0, "end": 4},
        ]
        self.assertEqual(len(merge(rules, neural)), 2)
        self.assertEqual(rules, before)

    def test_adapter_contract_and_stable_ids(self):
        message = "Пишите на lada@example.com."
        first = extract(message)
        second = extract(message)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        mention = first[0]
        self.assertEqual(mention.type, "EMAIL")
        self.assertEqual(mention.text, "lada@example.com")
        self.assertEqual(mention.confidence, None)
        self.assertEqual(mention.confidence_kind, "none")
        self.assertEqual(mention.source, "regex")

    def test_adapter_full_name_rules_and_inn_priority(self):
        text = "Меня зовут Лада Олеговна Макетова."
        value = "Лада Олеговна Макетова"
        start = text.index(value)
        full = extract(
            text,
            gliner_entities=[
                {
                    "label": "person",
                    "start": start,
                    "end": start + len(value),
                    "score": 0.9,
                }
            ],
        )
        self.assertEqual(
            [m.type for m in full], ["FIRST_NAME", "PATRONYMIC", "LAST_NAME"]
        )
        inn = extract("Тестовый ИНН: 000000123411.")
        self.assertEqual([m.type for m in inn], ["INN"])

    def test_person_on_pronoun_suppressed(self):
        text = 'Я работаю в ООО "Макет".'
        mentions = extract(
            text,
            gliner_entities=[
                {"label": "person", "start": 0, "end": 1, "score": 0.78},
            ],
        )
        types = [m.type for m in mentions]
        self.assertNotIn("PERSON", types)
        self.assertIn("ORGANIZATION", types)
        org = next(m for m in mentions if m.type == "ORGANIZATION")
        self.assertEqual(org.source, "regex")
        self.assertEqual(org.confidence_kind, "none")

    def test_synthetic_attributes_has_no_t_gold(self):
        with open(
            "benchmarks/pii_smoke/data/synthetic-attributes.json", encoding="utf-8"
        ) as fixture_file:
            fixture = json.load(fixture_file)
        self.assertTrue(fixture["synthetic"])
        self.assertEqual(fixture["contract_version"], CONTRACT_VERSION)
        self.assertNotIn("t_gold", fixture)


if __name__ == "__main__":
    unittest.main()
