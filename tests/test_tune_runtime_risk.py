"""Повторное применение риска: границы порога, владельцы, протокол и разбиение."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT)]
from evaluate_random_linker import alerts
from session_pii.components import Fact
from session_pii.risk import RiskPolicy
from tune_runtime_risk import Replay, kinds, objective, policy_from, prepare, replay_actual, split_families


class RiskTuningTests(unittest.TestCase):
    def test_owner_rules_and_unknown_dedup_are_different(self):
        def fact(kind, value, service=False):
            return Fact(kind, value, None, value, 1, service)
        facts = [fact('first_name', 'Тестимир'), fact('last_name', 'Макетов'),
                 fact('person', 'Макетов Тестимир'), fact('phone', '+7 000 111 22 33'),
                 fact('phone', '+7 000 111 22 33'), fact('email', 'service@example.invalid', True)]
        self.assertEqual(kinds(facts), {'fi': 1, 'phone': 1})
        self.assertEqual(kinds(facts, True), {'first_name': 1, 'last_name': 1, 'person': 1, 'phone': 1})
        facts.append(fact('phone', '+7 000 111 22 34'))
        self.assertEqual(kinds(facts, True)['phone'], 2)

    def test_vectorized_replay_matches_actual_risk_at_boundaries(self):
        texts = ['Тестимир', '30', '+7 000 111 22 33', '+7 000 111 22 34']
        kinds_ = ['first_name', 'age', 'phone', 'phone']
        messages = [dict(id=f'm{i}', text=t) for i, t in enumerate(texts, 1)]
        prediction = dict(first_alert=None, entities=[dict(message_id=f'm{i}', type=k,
            start=0, end=len(t), person_id='p1' if i < 3 else None, service_contact=False)
            for i, (t, k) in enumerate(zip(texts, kinds_), 1)])
        record = dict(session_id='s', family='f', bucket='b', expected_first_alert=None,
            gold=dict(messages=messages, case_flags=dict(annotation_uncertain=True)),
            calculator_assessment=dict(messages=[dict(annotation_uncertain=True)]))
        baseline = RiskPolicy.load()
        keys = sorted(baseline.weights)
        row = prepare(record, prediction, baseline)
        replay = Replay([row], keys)
        for threshold in (.1, .2, .3, .5, .6):
            params = np.array([round(baseline.weights[k]*100) for k in keys]), round(threshold*100), round(threshold*100)
            policy = policy_from(params, baseline, keys, 'test')
            actual = replay_actual(record, prediction, policy)
            self.assertEqual(int(replay.predict(*params)[0]), actual or 0)
        params = np.array([round(baseline.weights[k]*100) for k in keys]), 50, 60
        self.assertEqual(int(replay.predict(*params)[0]), 0)  # 0.6 == 0.6: no alert

    def test_fast_objective_matches_published_first_alert_protocol(self):
        gold = np.array([0, 2, 3, 4, 5, 0])
        pred = np.array([2, 2, 2, 5, 0, 0])
        measured = alerts([int(v) or None for v in gold], [int(v) or None for v in pred])
        self.assertEqual(objective(gold, pred), (measured['timely']['f2'], measured['session']['f2']))
        self.assertEqual(measured['timely']['fp'], 2)
        self.assertEqual(measured['timely']['fn'], 2)

    def test_split_is_balanced_deterministic_and_family_disjoint(self):
        rows = [dict(session_id=f'{family}-{i}', family=f'f{family}', bucket='b',
                     expected=None if i%2 else 3) for family in range(30) for i in range(6)]
        train, test = split_families(rows, 7)
        self.assertEqual((len(train), len(test)), (90, 90))
        self.assertFalse({r['family'] for r in train} & {r['family'] for r in test})
        self.assertEqual((train, test), split_families(rows, 7))


if __name__ == '__main__':
    unittest.main()
