"""Проверки статистического анализа на синтетических категориях."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from analyze_vk_annotation_evidence import (fit_common_pairs, score_common_pairs,
    common_pair_holdout, resolution, load_frozen)


class AnnotationEvidenceTests(unittest.TestCase):
    def test_selected_cell_space_is_normalized_with_other(self):
        rows = [(1, 'f1', 'i1')] * 10 + [(1, 'f2', 'i2')] * 10
        models = fit_common_pairs(rows, 5)
        independent = score_common_pairs(rows, models, 0)
        joint = score_common_pairs(rows, models, .9)
        self.assertAlmostEqual(independent['mean_log_loss_bits'], 2)
        self.assertLess(joint['mean_log_loss_bits'], 2)
        unseen = score_common_pairs([(1, 'never-seen', 'never-seen')], models, .9)
        self.assertAlmostEqual(unseen['mean_log_loss_bits'], -__import__('math').log2(.05))

    def test_validation_selected_mixture_generalizes_on_correlated_synthetic_table(self):
        records = [{'sex': 1, 'surname': f, 'name': i} for f, i in [('f1', 'i1'), ('f2', 'i2')] for _ in range(1000)]
        result = common_pair_holdout(records, 20, 42)
        self.assertGreater(result['validation_selected_alpha'], 0)
        self.assertLess(result['test_joint_minus_independent_bits'], 0)
        self.assertEqual(result['test_rows'], 400)
        self.assertNotIn('f1', str(result))

    def test_rare_threshold_resolution_is_hypothetical_and_not_confidence_bound(self):
        result = resolution(300000, 150000000, 100)
        self.assertAlmostEqual(result['expected_sample_occurrences_at_threshold'], .2)
        self.assertAlmostEqual(result['people_per_one_sample_occurrence_if_representative'], 500)
        self.assertTrue(result['population_is_illustrative_not_verified_Russia_total'])

    def test_frozen_manifest_ignores_new_pages_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = {'returned_items': 1, 'records': [{'account_id': 1, 'surname': 'synthetic-f', 'name': 'synthetic-i'}]}
            raw = json.dumps(page).encode()
            (root / 'page-0.json').write_bytes(raw)
            manifest = {'plan': {'offsets': [0, 1000]}, 'files': [{'name': 'page-0.json', 'sha256': hashlib.sha256(raw).hexdigest()}]}
            path = root / 'manifest.json'
            path.write_text(json.dumps(manifest))
            (root / 'page-1000.json').write_bytes(raw)
            _, all_rows, planned, counts = load_frozen(root, path)
            self.assertEqual(len(all_rows), 1)
            self.assertEqual(counts['current_plan_pages'], 1)
            (root / 'page-0.json').write_text('{}')
            with self.assertRaises(ValueError):
                load_frozen(root, path)


if __name__ == '__main__':
    unittest.main()
