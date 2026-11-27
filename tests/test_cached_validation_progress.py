"""Ошибки и остановка процесса в локальном отчёте."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from watch_cached_validation import render, snapshot


class CachedValidationProgressTests(unittest.TestCase):
    def test_missing_predictions_are_not_negative_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run/'input-snapshot.json').write_text(json.dumps({'records': [{'session_id': 'synthetic-test'}]}))
            (run/'failures').mkdir()
            (run/'failures/synthetic-test.json').write_text(json.dumps({'session_id': 'synthetic-test', 'error': 'invalid response'}))
            data = snapshot(run)
            self.assertEqual(data['completed'], 0)
            self.assertEqual(data['unresolved_failures'], 1)
            self.assertNotIn('partial_metrics', data)
            self.assertFalse(data['worker_alive'])
            self.assertIn('не обнаружен', render(data, run, 15))

    def test_historical_failure_outside_current_snapshot_is_not_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run/'input-snapshot.json').write_text('{"records": []}')
            (run/'failures').mkdir()
            (run/'failures/old.json').write_text(json.dumps({'session_id': 'old', 'error': 'old error'}))
            data = snapshot(run)
            self.assertEqual(data['pending'], 0)
            self.assertEqual(data['unresolved_failures'], 0)


if __name__ == '__main__':
    unittest.main()
