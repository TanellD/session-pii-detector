"""Совместная принадлежность через материализацию LLM и расчёт префиксов."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from annotation_contract import assess, validate
from annotation_calculator import Calculator
from run_compatible_annotation_pilot import materialize_quotes, quote_schema
from run_sequential_annotation import merge_step, step_payload
from compare_annotation_models import groups


def document():
    phone = '+7 (000) 000-00-01'
    email = 'synthetic@example.invalid'
    return dict(schema_version='annotation-mvp-v2', session_id='shared-synthetic',
                messages=[dict(id='m1', text=phone), dict(id='m2', text=email)],
                entities=[dict(id='e1', message_id='m1', start=0, end=len(phone),
                               type='phone', person_id=['p1', 'p2']),
                          dict(id='e2', message_id='m2', start=0, end=len(email),
                               type='email', person_id='p1')],
                case_flags=dict(is_example=False, annotation_uncertain=False, clarification_reason=None))


class MultiOwnerAnnotationTests(unittest.TestCase):
    def test_shared_fact_does_not_merge_profiles_or_become_unknown(self):
        doc = document()
        result = assess(doc, Calculator(policy_only=True))
        self.assertEqual(set(result['people']), {'p1', 'p2'})
        self.assertEqual(result['unknown_entity_ids'], [])
        self.assertEqual(result['people']['p1']['alert_reason'], 'combination_rule')
        self.assertNotEqual(result['people']['p2']['alert_reason'], 'combination_rule')
        self.assertEqual(result['first_alert']['message_index'], 2)
        self.assertEqual(len(result['first_alert']['context_entity_ids']),
                         len(set(result['first_alert']['context_entity_ids'])))
        self.assertEqual(doc, document())

    def test_invalid_owner_sets_rejected(self):
        for owners in ([], ['p1', 'p1'], ['p1', None], [''], [3], 3):
            doc = document()
            doc['entities'][0]['person_id'] = owners
            with self.subTest(owners=owners), self.assertRaises(ValueError):
                validate(doc)

    def test_llm_quotes_sequential_merge_and_schema(self):
        doc = document()
        source = {k: doc[k] for k in ('session_id', 'messages')}
        source['messages'] = source['messages'][:1]
        raw = dict(schema_version='annotation-quotes-v1', **source,
                   case_flags=doc['case_flags'], entities=[dict(id='e1', message_id='m1',
                   type='phone', person_id=['p1', 'p2'], mention=source['messages'][0]['text'], occurrence=1)])
        self.assertEqual(materialize_quotes(raw, source)['entities'][0]['person_id'], ['p1', 'p2'])
        previous, _ = merge_step(raw, step_payload(source, 1, None))
        self.assertEqual(previous['entities'][0]['person_id'], ['p1', 'p2'])
        schema = quote_schema()['properties']['entities']['items']['properties']['person_id']
        self.assertTrue(any(s['type'] == 'array' for s in schema['anyOf']))
        second = dict(schema_version='annotation-quotes-v1', session_id=doc['session_id'],
                      messages=doc['messages'], case_flags=doc['case_flags'],
                      entities=[dict(id='e2', message_id='m2', type='email', person_id='p1',
                                     mention=doc['messages'][1]['text'], occurrence=1)])
        merged, result = merge_step(second, step_payload(doc, 2, previous))
        self.assertEqual(merged['entities'][0], previous['entities'][0])
        self.assertEqual(result['first_alert']['message_index'], 2)

    def test_owner_comparison_is_order_invariant_but_keeps_multiplicity(self):
        doc = document()
        swapped = copy.deepcopy(doc)
        swapped['entities'][0]['person_id'].reverse()
        self.assertEqual(groups(doc), groups(swapped))
        shared = document()
        shared['entities'] = shared['entities'][:1]
        single = copy.deepcopy(shared)
        single['entities'][0]['person_id'] = 'p1'
        self.assertNotEqual(groups(shared), groups(single))

    def test_calculator_accepts_joint_owners(self):
        result = Calculator(policy_only=True).session([
            dict(type='phone', value='+7 (000) 000-00-01', person_id=['p1', 'p2'], complete=True, usage='personal'),
            dict(type='email', value='synthetic@example.invalid', person_id='p1', complete=True, usage='personal')])
        self.assertEqual(result['first_alert_attribute'], 2)
        self.assertEqual(set(result['people']), {'p1', 'p2'})
