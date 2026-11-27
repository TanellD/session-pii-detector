"""Проверки локального replay и протокола FP0 без моделей/API."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[1]/'src')]
from calibrate_cached_risk import measured, search
from tune_runtime_risk import Replay, prepare, replay_actual
from session_pii.risk import RiskPolicy
from session_pii.models import MentionOrigin, Message, Span
from run_cached_validation import CachedDetector


class CachedRiskCalibrationTests(unittest.TestCase):
    def test_ner_cache_roundtrip_preserves_strict_provenance(self):
        text = '+7 (000) 100-12-34'
        span = Span(type='phone', start=0, end=len(text), provenance=(
            MentionOrigin(mention_id='synthetic-unit', source='regex', detector_version='test'),))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/(hashlib.sha256(text.encode()).hexdigest()+'.json')
            path.write_text(json.dumps(dict(spans=[span.model_dump(mode='json')], complete=True, reasons=[])), encoding='utf-8')
            cached = CachedDetector(Path(directory)).detect(Message(message_id=1, text=text))
            self.assertEqual(cached.spans, (span,))
            self.assertTrue(cached.complete)

    def test_threshold_replay_matches_runtime_without_new_linker(self):
        name, phone = 'Макетимир', '+7 (000) 100-12-34'
        record = dict(session_id='synthetic-cache-unit', family='unit', bucket='1-short', expected_first_alert=None,
                      calculator_assessment=dict(messages=[dict(annotation_uncertain=True)]),
                      gold=dict(messages=[dict(id='m1', text=name), dict(id='m2', text=phone)],
                                case_flags=dict(annotation_uncertain=True)))
        prediction = dict(first_alert=None, entities=[
            dict(message_id='m1', start=0, end=len(name), type='first_name', person_id='p1'),
            dict(message_id='m2', start=0, end=len(phone), type='phone', person_id='p1')])
        baseline = RiskPolicy(version='test', person_threshold=.5, unknown_threshold=.5, weights=dict(first_name=.1, phone=.3))
        replay = Replay([prepare(record, prediction, baseline)], ['first_name', 'phone'])
        for threshold, expected in [(.5, None), (.4, None), (.35, 2)]:
            policy = baseline.model_copy(update=dict(person_threshold=threshold))
            actual = replay_actual(record, prediction, policy)
            fast = int(replay.predict(np.array([10, 30]), round(threshold*100), 50)[0]) or None
            self.assertEqual(actual, expected)
            self.assertEqual(fast, actual)
        initial = (np.array([10, 30]), 50, 50)
        selected, joint, count = search(replay, initial, 0, 1, thresholds_only=True)
        np.testing.assert_array_equal(selected[0], initial[0])
        np.testing.assert_array_equal(joint[0], initial[0])
        self.assertEqual(selected[1:], joint[1:])
        self.assertGreater(count, 1)

    def test_fp0_excludes_uncertain_negatives_and_timely_keeps_early_fp(self):
        rows = [dict(expected=None, uncertain=False), dict(expected=None, uncertain=True),
                dict(expected=3, uncertain=False)]
        result = measured(rows, [None, 1, 2])
        self.assertEqual(result['fp0'], 0)
        self.assertEqual(result['fp_negative_all'], .5)
        self.assertEqual(result['fp0_denominator'], 1)
        self.assertEqual(result['timely']['fp'], 2)
        self.assertEqual(result['timely']['fn'], 0)


if __name__ == '__main__':
    unittest.main()
