"""Численный порог и качественное правило могут сработать одновременно."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT)]
from profile_alert_reasons import reasons


class AlertReasonsTests(unittest.TestCase):
    def case(self, rows):
        text, entities = '', []
        for kind, value, owner in rows:
            start = len(text)
            text += value + ' '
            entities.append(dict(message_id='m1', type=kind, start=start, end=start + len(value), person_id=owner))
        return reasons(dict(messages=[dict(id='m1', text=text)]), entities, 1)

    def test_contacts_have_both_rules_but_one_primary_reason(self):
        result = self.case([('phone', '+7 (000) 123-45-67', 'a'), ('email', 'sample@example.invalid', 'a')])
        self.assertEqual(result['primary'], 'combination_rule')
        self.assertEqual(result['rules'], ['combination_rule', 'score_rule'])
        self.assertEqual(result['categories'], ['phone_email'])

    def test_orange_and_yellow_can_trigger_unknown_score_without_combination(self):
        result = self.case([('phone', '+7 (000) 123-45-67', None), ('person', 'Пробный', None),
                            ('organization', 'МакетА', None), ('organization', 'МакетБ', None)])
        self.assertEqual(result['primary'], 'score_rule')
        self.assertEqual(result['categories'], ['score_only'])
        self.assertEqual(result['assessments'][0]['score'], .6)
        self.assertTrue(result['assessments'][0]['unknown'])


if __name__ == '__main__':
    unittest.main()
