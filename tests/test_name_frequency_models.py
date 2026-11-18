"""Синтетические агрегаты: независимость, тройная зависимость и коллизии ключей."""

from io import BytesIO
import itertools
import math
from pathlib import Path
import sys
import unittest

import pyarrow as arrow
import pyarrow.parquet as parquet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_name_frequencies import audit
from compare_name_frequency_models import binary_association, joint_diagnostics


def rows(keys):
    return [{"surname": "F" + str(a + 1), "name": "I" + str(b + 1),
             "patronymic": "O" + str(c + 1), "count": 10} for a, b, c in keys]


class NameFrequencyModelsTests(unittest.TestCase):
    def test_independent_table_has_unit_lift(self):
        result = joint_diagnostics(rows(itertools.product(range(2), repeat=3)))
        self.assertAlmostEqual(result["total_correlation_bits"], 0)
        self.assertAlmostEqual(result["observed_cell_lift_min"], 1)
        self.assertAlmostEqual(result["all_cells_probability_l2_squared"], 0)

    def test_pairwise_independence_does_not_imply_triple_independence(self):
        result = joint_diagnostics(rows([(a, b, a ^ b) for a in range(2) for b in range(2)]))
        for mi in result["pair_mutual_information_bits"].values():
            self.assertAlmostEqual(mi, 0)
        self.assertAlmostEqual(result["total_correlation_bits"], 1)
        self.assertAlmostEqual(result["weighted_log2_lift_bits"], 1)
        self.assertAlmostEqual(result["observed_cell_lift_min"], 2)
        self.assertAlmostEqual(result["all_cells_probability_l2_squared"], 0.125)

    def test_duplicate_cells_and_nonanonymous_labels_rejected(self):
        cell = rows([(0, 0, 0)])[0]
        with self.assertRaises(ValueError):
            joint_diagnostics([cell, cell])
        for value in ("[СИНТЕТИЧЕСКОЕ ИМЯ]", "F0", "F-1"):
            with self.assertRaises(ValueError):
                joint_diagnostics([{**cell, "surname": value}])

    def test_binary_covariance_is_for_indicators(self):
        result = binary_association(100, 50, 50, 50)
        self.assertAlmostEqual(result["lift"], 2)
        self.assertAlmostEqual(result["binary_indicator_covariance"], 0.25)
        self.assertAlmostEqual(result["binary_indicator_phi"], 1)
        with self.assertRaises(ValueError):
            binary_association(100, 10, 10, 11)

    def test_audit_distinguishes_spelling_collisions_from_selected_field_duplicates(self):
        buffer = BytesIO()
        parquet.write_table(arrow.table({"text": ["СИНТЕТИКА-А", "синтетика-а", "СИНТЕТИКА-Б", "СИНТЕТИКА-Б"],
                                         "count": [2, 3, 4, 4], "gender": ["u"] * 4}), buffer)
        result = audit(buffer.getvalue())["normalization_audit"]
        self.assertEqual(result["distinct_spelling_collision_groups"], 1)
        self.assertEqual(result["same_raw_spelling_multiple_rows_groups"], 1)
        self.assertEqual(result["exact_text_count_gender_duplicate_groups"], 1)
        self.assertEqual(result["source_count_mass_in_normalized_collision_groups"], 13)


if __name__ == "__main__":
    unittest.main()
