"""Проверки накопления риска отдельно от качества NER и линкера."""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]

from session_pii.components import Fact
from session_pii.models import Verdict, Message
from session_pii.pipeline import Pipeline
from session_pii.risk import RiskEvaluator, RiskPolicy
from session_pii.components import DetectionResult, LinkResult


def facts(*types, person='p1'):
    return tuple(Fact(kind, f'synthetic-{i}', person, f'e{i}', i+1) for i,kind in enumerate(types))


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = RiskEvaluator()

    def test_config_weights_match_class_zones(self):
        classes = json.loads((ROOT/'configs/annotation-classes.json').read_text(encoding='utf-8'))
        weights = self.evaluator.policy.weights
        for kind,zone in classes['classes'].items():
            self.assertEqual(weights[kind],{'yellow':0.1,'orange':0.3,'red':1.0}[zone])
        for kind in classes['derived_only']:
            self.assertEqual(weights[kind],0.3)

    def test_strict_threshold(self):
        at = self.evaluator.assess(facts('first_name','sex','age','geography','organization'))
        self.assertEqual(at.risk_score,0.5)
        self.assertFalse(at.rules)
        above = self.evaluator.assess(facts('first_name','sex','age','geography','organization','occupation'))
        self.assertEqual(above.risk_score,0.6)
        self.assertEqual(above.rules,('score_rule',))

    def test_names_replace_parts_without_double_counting(self):
        for parts in [('first_name','last_name'), ('first_name','patronymic'),
                      ('last_name','patronymic'), ('first_name','last_name','patronymic')]:
            with self.subTest(parts=parts):
                result = self.evaluator.assess(facts(*parts))
                self.assertEqual(result.risk_score,0.3)
                self.assertFalse(result.rules)

    def test_known_repeats_and_replacements_do_not_increase_score(self):
        result = self.evaluator.assess(facts('email','email','email'))
        self.assertEqual(result.risk_score,0.3)
        self.assertFalse(result.rules)

    def test_unknown_duplicates_but_not_different_values_are_removed(self):
        original = facts('email',person=None)[0]
        duplicate = Fact('email',original.value,None,'repeat',2)
        result = self.evaluator.assess((original,duplicate),unknown=True)
        self.assertEqual(result.risk_score,0.3)
        result = self.evaluator.assess(facts('email','email',person=None),unknown=True)
        self.assertEqual(result.risk_score,0.6)
        self.assertEqual(result.rules,('score_rule',))

    def test_unknown_has_no_combination_rule_even_with_lower_weights(self):
        policy = self.evaluator.policy.model_copy(update={'unknown_threshold':1.0})
        evaluator = RiskEvaluator(policy)
        result = evaluator.assess(facts('phone','email',person=None),unknown=True)
        self.assertFalse(result.rules)
        self.assertEqual(evaluator.assess(facts('phone','email')).rules,
                         ('combination_rule','score_rule'))

    def test_not_every_orange_pair_is_qualitative_combination(self):
        result = self.evaluator.assess(facts('address','phone'))
        self.assertEqual(result.rules,('score_rule',))

    def test_red_without_configured_weights_or_high_threshold(self):
        policy = RiskPolicy(version='test',person_threshold=10.0,unknown_threshold=10.0,weights={})
        evaluator = RiskEvaluator(policy)
        for unknown in (False,True):
            result = evaluator.assess(facts('individual_inn'),unknown=unknown)
            self.assertIsNone(result.risk_score)
            self.assertEqual(result.rules,('red_rule',))

    def test_all_historical_people_are_evaluated_and_kept_separate(self):
        class EmptyDetector:
            def detect(self, message):
                return DetectionResult()
        class EmptyLinker:
            def link(self, message, candidates, known_facts):
                return LinkResult(())
        pipeline = Pipeline(EmptyDetector(),EmptyDetector(),EmptyLinker())
        history = facts('email',person='p1') + facts('phone',person='p2')
        step = pipeline.process(Message(message_id=3,text='Спасибо.'),history,Verdict())
        self.assertFalse(step.verdict.current_alert)
        self.assertEqual(set(step.verdict.person_risks),{'p1','p2'})
        red_history = history + facts('individual_inn',person='p3')
        step = pipeline.process(Message(message_id=3,text='Спасибо.'),red_history,Verdict())
        self.assertTrue(step.verdict.current_alert)
        self.assertEqual(step.verdict.first_alert_reason,'red_rule')

    def test_missing_weight_is_explicit_and_policy_is_validated(self):
        evaluator = RiskEvaluator(RiskPolicy(version='test',person_threshold=0.5,unknown_threshold=0.5,weights={}))
        result = evaluator.assess(facts('email'))
        self.assertEqual(result.missing_weights,('email',))
        self.assertIsNone(result.risk_score)
        for weights in ({'email':-0.1}, {'email':float('nan')}, {'unknown_type':0.1}):
            with self.assertRaises(ValueError):
                RiskPolicy(version='invalid',person_threshold=0.5,unknown_threshold=0.5,weights=weights)


if __name__ == '__main__':
    unittest.main()
