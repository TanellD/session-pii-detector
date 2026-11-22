from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from annotation_calculator import Calculator
from annotation_contract import assess


class OnboardingTests(unittest.TestCase):
    def test_policy_only_does_not_load_external_reference_files(self):
        with patch("annotation_calculator.load_reference", side_effect=AssertionError("External reference read")):
            calculator = Calculator(policy_only=True)
        self.assertIsNone(calculator.occupations)
        self.assertEqual(calculator.geo["rows"], [])
        self.assertEqual({r["housing_type"] for r in calculator.housing["rows"]},
                         {"apartment", "individual_house", "communal_apartment"})

    def test_missing_occupation_statistics_remain_unknown(self):
        text = "Моя профессия — программист."
        start = text.index("программист")
        doc = {"schema_version": "annotation-mvp-v2", "session_id": "synthetic-no-references",
               "messages": [{"id": "m1", "text": text}],
               "entities": [{"id": "e1", "message_id": "m1", "start": start,
                             "end": start+len("программист"), "type": "occupation", "person_id": "p1"}],
               "case_flags": {"is_example": False, "annotation_uncertain": False, "clarification_reason": None}}
        result = assess(doc, Calculator(policy_only=True))
        self.assertIsNone(result["first_alert"])
        self.assertEqual(result["case_flags"]["clarification_reason"], "insufficient_statistics")


if __name__ == "__main__":
    unittest.main()
