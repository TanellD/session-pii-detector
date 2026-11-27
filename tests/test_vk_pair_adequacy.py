"""Проверка достаточности на синтетических категориальных данных."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from assess_vk_pair_sample import holdout_pair, assess, observed_lift


class VkPairAdequacyTests(unittest.TestCase):
    def test_lift_matches_independent_and_associated_synthetic_tables(self):
        independent = [(f, i) for f in ("f1", "f2") for i in ("i1", "i2")] * 10
        self.assertEqual(observed_lift(independent)["thresholds"][0]["median_joint_over_product"], 1)
        associated = [("f1", "i1"), ("f2", "i2")] * 10
        self.assertEqual(observed_lift(associated)["thresholds"][0]["median_joint_over_product"], 2)
        self.assertIsNone(observed_lift([])["thresholds"][0]["median_joint_over_product"])

    def test_holdout_is_order_invariant_and_reports_unseen_pairs(self):
        rows = [(f"f{i}", f"i{i}") for i in range(100)]
        a = holdout_pair(rows, 1000, 42)
        self.assertEqual(a, holdout_pair(list(reversed(rows)), 1000, 42))
        self.assertEqual(a["pair_unseen_in_train"], a["test_rows"])
        self.assertGreater(a["joint_minus_independent_bits"], 0)

    def test_repeated_pairs_are_counted_without_claiming_population_validation(self):
        rows = [{"surname": "synthetic-f", "name": "synthetic-i", "sex": None} for _ in range(10)]
        result = assess(rows)
        self.assertEqual(result["pair_cells_count_ge_5"], 1)
        self.assertEqual(result["max_pair_count"], 10)
        self.assertFalse(result["full_patronymics_available"])
        self.assertNotIn("synthetic-f", str(result))


if __name__ == "__main__":
    unittest.main()
