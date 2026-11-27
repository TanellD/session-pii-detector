"""Отдельный эталон: неизменность входа инференса и префиксная оценка."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src')]
from annotation_calculator import Calculator
from relabel_weight_300 import ReviewedCalculator, digest, relabel, validate_overlay


class RelabelTests(unittest.TestCase):
    def source(self):
        texts = ['Иван', 'работает бухгалтером', 'ООО «Макет»']
        kinds = ['first_name', 'occupation', 'organization']
        values = ['Иван', 'бухгалтером', 'ООО «Макет»']
        messages = [dict(id=f'm{i}', text=t) for i, t in enumerate(texts, 1)]
        entities = [dict(id=f'e{i}', message_id=f'm{i}', type=k, person_id='p1',
                         start=t.index(v), end=t.index(v)+len(v))
                    for i, (t, k, v) in enumerate(zip(texts, kinds, values), 1)]
        return dict(version='attribute-combinations-300-v1', synthetic_only=True,
            records=[dict(session_id='s1', expected_first_alert=None,
                gold=dict(schema_version='annotation-mvp-v2', session_id='s1', messages=messages,
                    entities=entities, case_flags=dict(is_example=False,
                        annotation_uncertain=False, clarification_reason=None)))])

    def test_overlay_rejects_text_span_and_owner_changes(self):
        source = self.source()
        corrected = deepcopy(source)
        corrected['original_content_sha256'] = digest(source)
        validate_overlay(source, corrected)
        for field, value in [('person_id', 'p2'), ('start', 1), ('type', 'person')]:
            altered = deepcopy(corrected)
            altered['records'][0]['gold']['entities'][0][field] = value
            with self.assertRaises(ValueError):
                validate_overlay(source, altered)
        corrected['records'][0]['gold']['messages'][0]['text'] = 'Changed'
        with self.assertRaises(ValueError):
            validate_overlay(source, corrected)

    def test_threshold_and_ambiguity_use_existing_calculator(self):
        calculator = ReviewedCalculator()
        facts = [dict(type='occupation', value='бухгалтером', person_id='p1'),
                 dict(type='organization', value='ООО «Макет»', person_id='p1')]
        actual = calculator.person(facts)
        reference = Calculator().person([{**facts[0], 'value': 'бухгалтер'}, facts[1]])
        self.assertEqual(actual, reference)
        self.assertEqual(facts[0]['value'], 'бухгалтером')
        ambiguous = calculator.person([{**facts[0], 'value': 'инженером'}, facts[1]])
        self.assertIsNone(ambiguous['candidate_count'])
        self.assertIsNone(ambiguous['alert'])

    def test_relabel_preserves_input_and_uses_only_prefix_evidence(self):
        source = self.source()
        before = deepcopy(source)
        revised = relabel(source, ReviewedCalculator())
        self.assertEqual(source, before)
        row = revised['records'][0]
        self.assertEqual(row['expected_first_alert'], 3)
        self.assertIsNone(row['previous_author_labels']['expected_first_alert'])
        steps = row['calculator_prefix_ledger']
        self.assertEqual([s['rule_alert'] for s in steps], [False, False, True])
        self.assertTrue(steps[0]['annotation_uncertain'])
        self.assertTrue(steps[-1]['people']['p1']['candidate_count'] <= 100)
        self.assertEqual(revised['positive_sessions'], 1)
        validate_overlay(source, revised)


if __name__ == '__main__':
    unittest.main()
