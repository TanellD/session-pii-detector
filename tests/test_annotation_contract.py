"""Минимальная разметка: границы, отсутствие допполей, неизвестные лица, префиксы."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from annotation_contract import validate, assess, check_cases
from annotation_calculator import Calculator
from build_annotation_size_fallbacks import build
from build_name_part_statistics import frequencies


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(Path("tests/fixtures/annotation-mvp-cases.json").read_text(encoding="utf-8"))["cases"]
        cls.by_id = {case["id"]: case["annotation"] for case in cls.cases}
        cls.calculator = Calculator()

    def test_all_synthetic_cases(self):
        self.assertEqual(check_cases("tests/fixtures/annotation-mvp-cases.json")["status"], "passed")

    def test_unknown_entities_never_form_one_unknown_person(self):
        result = assess(self.by_id["unassigned_contacts"], self.calculator)
        self.assertEqual(result["people"], {})
        self.assertEqual(len(result["unknown_entity_ids"]), 2)
        self.assertIsNone(result["first_alert"])

    def test_unlinked_contacts_do_not_make_verdict_uncertain(self):
        doc = copy.deepcopy(self.by_id["unassigned_contacts"])
        doc["case_flags"] = {"is_example": False, "annotation_uncertain": True,
                             "clarification_reason": "person_attribution"}
        result = assess(doc, self.calculator)
        self.assertEqual(result["messages"][-1]["assessment"], "not_triggered")
        self.assertTrue(result["case_flags"]["annotation_uncertain"])
        self.assertEqual(result["case_flags"]["clarification_reason"], "person_attribution")
        self.assertTrue(all(e["person_id"] is None for e in doc["entities"]))

    def test_known_negative_preserves_input_missing_statistics_flag(self):
        doc = copy.deepcopy(self.by_id["unassigned_contacts"])
        doc["case_flags"] = {"is_example": False, "annotation_uncertain": True,
                             "clarification_reason": "insufficient_statistics"}
        doc["entities"] = []
        result = assess(doc, self.calculator)
        self.assertTrue(result["case_flags"]["annotation_uncertain"])

    def test_v2_rejects_full_name_and_relationship_as_annotation_types(self):
        for kind in ("fio", "relationship"):
            doc = copy.deepcopy(self.by_id["io_phone"])
            doc["entities"][0]["type"] = kind
            with self.assertRaises(ValueError):
                validate(doc)

    def test_v2_uses_case_flags_without_suppressing_example_alert(self):
        result = assess(self.by_id["explicit_example_with_alert"], self.calculator)
        self.assertTrue(result["case_flags"]["is_example"])
        self.assertTrue(result["session_alert"])
        self.assertEqual(result["first_alert"]["reason"], "combination_rule")
        uncertain = assess(self.by_id["full_name_alone"], self.calculator)
        self.assertTrue(uncertain["case_flags"]["annotation_uncertain"])
        self.assertEqual(uncertain["case_flags"]["clarification_reason"], "insufficient_statistics")

    def test_v2_separate_parts_and_no_preposition_inside_boundary(self):
        doc = copy.deepcopy(self.by_id["full_name_alone"])
        self.assertEqual({e["type"] for e in doc["entities"]}, {"last_name", "first_name", "patronymic"})
        self.assertEqual({e["person_id"] for e in doc["entities"]}, {"p1"})
        result = assess(doc, self.calculator)
        self.assertEqual(result["people"]["p1"]["active_name_marker"]["type"], "fio")
        doc = {"schema_version": "annotation-mvp-v2", "session_id": "synthetic-preposition",
               "case_flags": {"is_example": False, "annotation_uncertain": False, "clarification_reason": None},
               "messages": [{"id": "m1", "text": "в Макетограде"}],
               "entities": [{"id": "e1", "message_id": "m1", "start": 0, "end": 13, "type": "geography", "person_id": "p1"}]}
        with self.assertRaises(ValueError):
            validate(doc)

    def test_unknown_history_is_not_reassigned_and_unnamed_person_can_exist(self):
        doc = self.by_id["no_unknown_reassignment"]
        result = assess(doc, self.calculator)
        self.assertFalse(result["session_alert"])
        self.assertIsNone(doc["entities"][0]["person_id"])
        self.assertIn(doc["entities"][0]["id"], result["unknown_entity_ids"])
        result = assess(self.by_id["unnamed_person_created"], self.calculator)
        self.assertTrue(result["session_alert"])
        self.assertEqual(set(result["people"]), {"p_guest"})

    def test_extra_fields_invalid_boundaries_and_overlaps_rejected(self):
        source = self.by_id["io_phone"]
        for mutation in (lambda e: e.update(complete=True), lambda e: e.update(start=True),
                         lambda e: e.update(end=9999), lambda e: e.update(person_id="")):
            doc = copy.deepcopy(source)
            mutation(doc["entities"][0])
            with self.assertRaises(ValueError):
                validate(doc)
        doc = copy.deepcopy(source)
        doc["entities"].append({**doc["entities"][0], "id": "overlap"})
        with self.assertRaises(ValueError):
            validate(doc)

    def test_initials_are_rejected_as_name_parts(self):
        doc = copy.deepcopy(self.by_id["initials_not_annotated"])
        text = doc["messages"][0]["text"]
        start = text.index("Т.")
        doc["entities"].append({"id": "initial", "message_id": "m1", "start": start,
                                "end": start + 2, "type": "first_name", "person_id": "p1"})
        with self.assertRaises(ValueError):
            validate(doc)

    def test_same_message_is_assessed_after_its_correction(self):
        doc = {"schema_version": "annotation-mvp-v1", "session_id": "synthetic-one-message",
               "messages": [{"id": "m1", "text": "Программист. Мой работодатель ООО Макет. Уточнение: должность Неизвестная."}], "entities": []}
        for i, (value, kind) in enumerate((("Программист", "occupation"), ("ООО Макет", "organization"), ("Неизвестная", "occupation"))):
            start = doc["messages"][0]["text"].index(value)
            doc["entities"].append({"id": f"e{i}", "message_id": "m1", "start": start, "end": start + len(value), "type": kind, "person_id": "p1"})
        self.assertIsNone(assess(doc, self.calculator)["first_alert"])


class FallbackTests(unittest.TestCase):
    def test_name_parts_have_one_source_denominator_without_population_extrapolation(self):
        rows = [{"surname": "Вымышлов", "name": "Тестимир", "patronymic": "Макетович"},
                {"surname": "Образцов", "name": "Тестимир", "patronymic": "Примерович"}]
        result = frequencies(rows)
        self.assertEqual(result["parts"]["name"][0]["source_probability"], 1)
        self.assertEqual(result["parts"]["surname"][0]["source_probability"], .5)
        self.assertIsNone(result["population_estimate"])

    def source(self):
        return {"coverage_complete": True, "national_coverage_complete": False,
                "source_url": "https://example.invalid/synthetic", "reference_date": "synthetic",
                "unit": "settlement", "rows": [
                    {"id": "synthetic-1", "object_type": "derevnya", "region": "synthetic", "population": 1},
                    {"id": "synthetic-2", "object_type": "derevnya", "region": "synthetic", "population": 150}]}

    def test_weighted_mean_differs_from_median_and_no_national_claim(self):
        result = build(self.source())
        self.assertEqual(len(result["rows"]), 1)
        self.assertAlmostEqual(result["rows"][0]["resident_weighted_mean"], (1 + 150 ** 2) / 151)
        self.assertEqual(result["rows"][0]["object_median"], 75.5)
        self.assertFalse(result["approved_for_annotation"])

    def test_incomplete_coverage_and_duplicate_objects_are_rejected(self):
        source = self.source()
        source["coverage_complete"] = False
        with self.assertRaises(ValueError):
            build(source)
        source = self.source()
        source["rows"].append(source["rows"][0])
        with self.assertRaises(ValueError):
            build(source)


if __name__ == "__main__":
    unittest.main()
