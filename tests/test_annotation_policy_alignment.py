import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from annotation_calculator import Calculator
from annotation_contract import assess, validate, ANNOTATION_TYPES
from annotation_address_enrichment import build_enrichment
from estimate_address_population import estimate_resolved
from audit_annotation_consistency import document
from build_llm_annotation_pilot import CASES


class PolicyAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.calculator = Calculator(policy_only=True)

    def test_later_alert_preserves_prior_uncertainty_and_input(self):
        doc = document(next(c for c in CASES if c[0] == "pair_then_email"))
        before = assess({**doc, "messages": doc["messages"][:1],
                         "entities": [e for e in doc["entities"] if e["message_id"] == "m1"]}, self.calculator)
        after = assess(doc, self.calculator)
        self.assertEqual(before["messages"][0], after["messages"][0])
        self.assertFalse(after["messages"][0]["rule_alert"])
        self.assertTrue(after["messages"][0]["annotation_uncertain"])
        self.assertTrue(after["case_flags"]["annotation_uncertain"])
        self.assertEqual(after["first_alert"]["message_id"], "m3")
        self.assertFalse(doc["case_flags"]["annotation_uncertain"])

    def test_address_stage_is_replayable_bound_to_annotation_and_does_not_change_spans(self):
        doc = document(("synthetic", ["Testimir", "Synthetic apartment address"],
                       [(1, "Testimir", "first_name", "p1"),
                        (2, "Synthetic apartment address", "address", "p1")], False, None, None))
        original = copy.deepcopy(doc)
        calls = []
        def resolver(query):
            calls.append(query)
            return estimate_resolved(self.calculator, {"value": query, "completeness": "full",
                "has_flat": True, "flat_type": "кв", "resolved_level": "apartment"})
        self.assertFalse(assess(doc, self.calculator)["session_alert"])
        snapshot = build_enrichment(doc, self.calculator, resolver)
        result = assess(doc, self.calculator, address_enrichment=snapshot)
        self.assertTrue(result["session_alert"])
        self.assertEqual(result["first_alert"]["reason"], "candidate_count_rule")
        self.assertEqual(len(calls), 1)
        self.assertEqual(doc, original)
        doc["messages"][1]["text"] += " changed"
        with self.assertRaises(ValueError):
            assess(doc, self.calculator, address_enrichment=snapshot)

    def test_unlinked_address_is_not_sent_and_ambiguous_result_not_invented(self):
        doc = document(("synthetic", ["Synthetic address"],
                        [(1, "Synthetic address", "address", None)], False, None, None))
        def unexpected(_):
            self.fail("Unlinked address must not be sent")
        self.assertEqual(build_enrichment(doc, self.calculator, unexpected)["results"], {})
        doc["entities"][0]["person_id"] = "p1"
        result = build_enrichment(doc, self.calculator, lambda _: {"status": "selection_required", "reason": "choose_address"})
        self.assertNotIn("fact", result["results"]["e1"])
        self.assertFalse(assess(doc, self.calculator, address_enrichment=result)["session_alert"])

    def test_age_full_years_and_no_invented_statistics(self):
        doc = document(("synthetic", ["37 лет"], [(1, "37 лет", "age", "p1")], False, None, None))
        result = assess(doc, self.calculator)
        self.assertFalse(result["session_alert"])
        self.assertTrue(result["case_flags"]["annotation_uncertain"])
        self.assertIsNone(result["people"]["p1"]["candidate_count"])
        for value in ("около 37", "30–35 лет", "01.01.2000"):
            bad = document(("synthetic", [value], [(1, value, "age", "p1")], False, None, None))
            with self.assertRaises(ValueError):
                validate(bad)

    def test_class_catalog_matches_annotation_contract(self):
        catalog = json.loads(Path("configs/annotation-classes.json").read_text(encoding="utf-8"))
        self.assertEqual(set(catalog["classes"]), ANNOTATION_TYPES)

    def test_new_document_semantics_do_not_require_checksum_but_old_smoke_is_preserved(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from benchmarks.pii_smoke.regex_detector import predict
        text = "СНИЛС: 000-000-000 01"
        self.assertEqual(predict(text), [])
        extracted = predict(text, require_valid_checksum=False)
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0]["type"], "SNILS")
        self.assertEqual(text[extracted[0]["start"]:extracted[0]["end"]], "000-000-000 01")


if __name__ == "__main__":
    unittest.main()
