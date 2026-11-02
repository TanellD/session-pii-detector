"""Checks for diagnostic arithmetic, generated fixtures and result accounting."""

import copy
import unittest

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


if __name__ == "__main__":
    unittest.main()
