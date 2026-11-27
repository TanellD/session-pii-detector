"""Структурная проверка источников на синтетических строках, без сети."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_patronymic_source_formats import inspect_schema, inspect_csv


class PatronymicSourceFormatTests(unittest.TestCase):
    def test_optional_patronymic_is_not_invented_as_required(self):
        raw = b'<schema><attribute name="\xd0\x9e\xd1\x82\xd1\x87\xd0\xb5\xd1\x81\xd1\x82\xd0\xb2\xd0\xbe" use="optional"/></schema>'
        result = inspect_schema(raw)
        self.assertTrue(result['patronymic_field_found'])
        self.assertEqual(result['name_fields'][0]['use'], 'optional')

    def test_cp1251_csv_exports_counts_without_personal_strings(self):
        raw = 'ФИО;Статус\r\nСинтетический Тест Тестович;действует\r\n;нет\r\n'.encode('cp1251')
        result = inspect_csv(raw)
        self.assertEqual(result['data_rows'], 2)
        self.assertEqual(result['rows_with_nonempty_fullname'], 1)
        self.assertNotIn('Синтетический', str(result))
        self.assertFalse(result['personal_rows_saved'])


if __name__ == '__main__':
    unittest.main()
