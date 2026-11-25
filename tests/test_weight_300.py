"""Состав корпуса, чистые жёлтые сочетания и независимость авторских меток."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT)]
from build_weight_300 import build
from evaluate_weight_oracle import evaluate
from evaluate_gemma_linker import bucket_alerts


class Weight300Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary_root = ROOT / 'data/tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        cls.output = Path(cls.temp.name) / 'corpus'
        with patch('session_pii.risk.RiskPolicy.load', side_effect=AssertionError('Generator must not use runtime weights')):
            cls.manifest = build(cls.output)
        cls.source = json.loads((cls.output / 'snapshot.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_size_lengths_people_and_bucket_counts(self):
        self.assertEqual(len(self.source['records']), 300)
        self.assertEqual(self.manifest['buckets'], {'orange_yellow': 180, 'yellow_only': 80, 'control': 40})
        self.assertEqual(len({r['session_id'] for r in self.source['records']}), 300)
        for record in self.source['records']:
            self.assertTrue(5 <= len(record['gold']['messages']) <= 10)
            owners = set()
            for e in record['gold']['entities']:
                owners.update(e['person_id'] if isinstance(e['person_id'], list) else [e['person_id']] if e['person_id'] else [])
            self.assertEqual(len(owners), record['people'])

    def test_yellow_only_really_has_no_contacts_or_documents(self):
        forbidden = {'phone', 'email', 'address', 'passport', 'snils', 'individual_inn', 'bank_card'}
        rows = [r for r in self.source['records'] if r['bucket']=='yellow_only']
        self.assertEqual(len(rows), 80)
        for record in rows:
            self.assertFalse(forbidden & {e['type'] for e in record['gold']['entities']})
        self.assertTrue(any(r['expected_first_alert'] is not None for r in rows))
        self.assertTrue(any(r['expected_first_alert'] is None for r in rows))

    def test_oracle_can_disagree_with_author_labels(self):
        result = evaluate(self.output / 'snapshot.json')
        self.assertEqual(result['sessions'], 300)
        self.assertGreater(result['overall']['session']['fp'], 0)
        self.assertGreater(result['overall']['session']['fn'], 0)
        self.assertLess(result['overall']['timely']['f2'], result['overall']['session']['f2'])
        self.assertEqual(self.manifest['label_policy_status'], 'proposed_not_accepted_team_policy')
        self.assertFalse(self.manifest['service_weights_used_for_gold'])

    def test_bucket_metrics_keep_incomplete_coverage_and_timing(self):
        records={'a': {'bucket': 'yellow_only'}, 'b': {'bucket': 'yellow_only'},
                 'c': {'bucket': 'orange_yellow'}}
        cases=[dict(session_id='a',gold_first_alert=4,predicted_first_alert=2)]
        result=bucket_alerts(records,list(records),cases)
        self.assertEqual(result['yellow_only']['selected_sessions'],2)
        self.assertEqual(result['yellow_only']['completed_sessions'],1)
        self.assertEqual(result['yellow_only']['session']['tp'],1)
        self.assertEqual(result['yellow_only']['timely']['fp'],1)
        self.assertEqual(result['orange_yellow']['completed_sessions'],0)
        self.assertIsNone(result['orange_yellow']['session']['f2'])


if __name__ == '__main__':
    unittest.main()
