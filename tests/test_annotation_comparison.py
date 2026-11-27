import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_annotation_models import score, timely, groups


class AnnotationComparisonTests(unittest.TestCase):
    def test_early_positive_is_fp_only_and_late_is_fn(self):
        self.assertEqual(timely(3, 2), 'fp')
        self.assertEqual(timely(3, 4), 'fn')
        self.assertEqual(timely(3, 3), 'tp')
        self.assertEqual(timely(3, None), 'fn')
        self.assertEqual(timely(None, 2), 'fp')
        self.assertEqual(timely(None, None), 'tn')

    def test_empty_and_all_missed_metrics(self):
        self.assertIsNone(score(0, 0, 0)['f2'])
        self.assertEqual(score(0, 0, 2)['f2'], 0)
        self.assertAlmostEqual(score(2, 1, 1)['f2'], 2 / 3)

    def test_owner_ids_are_local_and_null_does_not_form_a_person(self):
        def doc(people):
            return {'entities': [dict(message_id='m1', start=i*2, end=i*2+1, type='first_name', person_id=p) for i, p in enumerate(people)]}
        self.assertEqual(groups(doc(['p1', 'p1'])), groups(doc(['other', 'other'])))
        self.assertNotEqual(groups(doc(['p1', 'p1'])), groups(doc(['a', 'b'])))
        self.assertNotEqual(groups(doc([None, None])), groups(doc(['a', 'a'])))
