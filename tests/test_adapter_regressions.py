"""Synthetic regressions for the agreed MVP adapter boundary."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmarks.adapter import _rules_impl as rules
from benchmarks.adapter.adapter import extract


def injected(text, fragment, kind='PERSON'):
    start = text.index(fragment)
    return extract(text, gliner_entities=[dict(label='synthetic', start=start,
                   end=start+len(fragment), score=.99)],
                   config={'labels': {'synthetic': kind}})


class AdapterRegressions(unittest.TestCase):
    def test_context_preserves_original_and_has_independent_provenance(self):
        text = 'Тестимир, фамилия Макетонов.'
        with patch.object(rules, 'NamesParser', None):
            result = injected(text, 'Тестимир')
        self.assertEqual([(m.type, m.text) for m in result],
                         [('PERSON', 'Тестимир'), ('LAST_NAME', 'Макетонов')])
        self.assertEqual(result[1].source, 'regex')
        self.assertIsNone(result[1].confidence)

    def test_context_without_model_and_dash_patronymic(self):
        result = extract('Моё отчество — Примерович.')
        self.assertEqual([(m.type, m.text) for m in result], [('PATRONYMIC', 'Примерович')])

    def test_unknown_multitoken_name_stays_person(self):
        text = 'Макетонов Тестимир Другомир Четвертович'
        with patch.object(rules, 'NamesParser', None):
            self.assertEqual([m.type for m in injected(text, text)], ['PERSON'])

    def test_parser_cannot_find_a_part_outside_model_span(self):
        class Parser:
            def parse(self, value):
                return dict(parsed=True, format='s', sn='Макетонов')
        with patch.object(rules, 'NamesParser', object()), patch.object(rules, 'get_names_parser', return_value=Parser()):
            result = injected('Тестимир и Макетонов', 'Тестимир')
        self.assertEqual([(m.type, m.text) for m in result], [('PERSON', 'Тестимир')])

    def test_phones_reject_partial_malformed_and_extra_digits_from_both_sources(self):
        for value in ('abc12', '+7 (000 000-00-00', '+7 000) 000-00-00',
                      '+7 (000) 000-00-00-12', '12 +7 (000) 000-00-00'):
            with self.subTest(value=value):
                self.assertNotIn('PHONE', [m.type for m in extract(value)])
                self.assertNotIn('PHONE', [m.type for m in injected(value, value, 'PHONE')])
        for value in ('+7 (000) 000-00-00', '80000000000', '+70000000000'):
            self.assertEqual([m.type for m in extract(value)], ['PHONE'])
            self.assertEqual([m.type for m in injected(value, value, 'PHONE')], ['PHONE'])

    def test_duplicates_do_not_drop_unrelated_candidates(self):
        def candidate(start, end):
            return dict(type='EMAIL', start=start, end=end, source='gliner', score=.9)
        result = rules.resolve_conflicts('aa bb', [candidate(0, 2), candidate(3, 5), candidate(0, 2)])
        self.assertEqual([(m['start'], m['end']) for m in result], [(0, 2), (3, 5)])

    def test_excluded_types_and_personal_synthetic_email(self):
        excluded = {'DATE_OF_BIRTH', 'RELATIONSHIP', 'SPECIAL_CATEGORY'}
        config = json.loads(Path('benchmarks/pii_smoke/config.json').read_text())
        self.assertFalse(excluded.intersection(config['labels'].values()))
        for kind in excluded:
            self.assertEqual(injected('Синтетика', 'Синтетика', kind), [])
        self.assertEqual([m.type for m in extract('Моя почта synthetic@example.invalid.')], ['EMAIL'])

    def test_checksum_is_extraction_filter_for_regex_and_model(self):
        for kind, text, value in (('INN', 'ИНН: 000000000031', '000000000031'),
                                  ('SNILS', 'СНИЛС: 000-000-000 16', '000-000-000 16'),
                                  ('CARD', 'карта: 0000 0000 0000 0001', '0000 0000 0000 0001')):
            self.assertNotIn(kind, [m.type for m in injected(text, value, kind)])
