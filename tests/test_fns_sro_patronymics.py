"""Проверки сбора СРО и преобразования ИО на вымышленных данных."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from study_fns_sro_patronymics import parse_sro, io_records, reference_rows, column_mapping, merge_sro_records, certificate_value


class FnsSroTests(unittest.TestCase):
    def test_certificate_in_text_and_conflicts_across_pages(self):
        self.assertEqual(certificate_value('synthetic label: 10-10-123'), '10-10-123')
        self.assertEqual(certificate_value('10-10-123 / 10-10-124'), '')
        a = {'surname': 'вымышлов', 'name': 'тестимир', 'patronymic': 'примерович', 'local_key': 'synthetic-a'}
        b = {**a, 'local_key': 'synthetic-b'}
        records, quality = merge_sro_records([a, a, b, {**b, 'name': 'макет'}])
        self.assertEqual(records, [a])
        self.assertEqual(quality['duplicate_rows_same_certificate_and_name'], 1)
        self.assertEqual(quality['conflicting_name_certificate_groups'], 1)

    def test_html_headers_without_tr_are_supported(self):
        headers = '<thead>' + ''.join(f'<th>{v}</th>' for v in
            ['ФИО', 'Дата вступления', 'Номер аттестата', 'Реестровый номер', 'Статус инженера']) + '</thead>'
        rows = ''.join(f'<tr><td>Вымышлов Тестимир Примерович</td><td>date</td><td>10-10-{i}</td><td>{i}</td><td>synthetic</td></tr>' for i in range(3))
        self.assertEqual(len(reference_rows('<table>' + headers + '<tbody>' + rows + '</tbody></table>')), 3)

    def test_no_header_csv_mapping_dedup_and_name_conflict(self):
        names = ['Вымышлов Тестимир Примерович', 'Тестова Примерина Примеровна', 'Образцов Макет Примерович']
        refs = ''.join(f'<tr><td>{name}</td><td>date</td><td>10-10-{i}</td><td>{i}</td><td>Состоит в СРО</td></tr>'
                       for i, name in enumerate(names, 1))
        html = '<table><tr><th>ФИО</th><th>Дата</th><th>Номер аттестата</th><th>Номер</th><th>Статус</th></tr>' + refs + '</table>'
        csv_rows = [f'{name};10-10-{i};synthetic-status;synthetic-extra' for i, name in enumerate(names, 1)]
        csv_rows += [csv_rows[0], 'Имитатов Макет Примерович;10-10-3;synthetic-status;synthetic-extra']
        result = parse_sro(('\n'.join(csv_rows)).encode(), html)
        self.assertEqual(result['raw_registry_rows'], 5)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['verified_mapping']['fio_start'], 0)
        self.assertEqual(result['verified_mapping']['certificate_column'], 1)
        self.assertEqual(result['quality']['duplicate_rows_same_certificate_and_name'], 1)
        self.assertEqual(result['quality']['conflicting_name_certificate_groups'], 1)
        self.assertEqual(set(result['records'][0]), {'surname', 'name', 'patronymic', 'local_key'})
        self.assertNotIn('10-10-1', str(result['records']))

    def test_unverified_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            column_mapping([['synthetic', 'unrelated']], [{'fio': 'different', 'certificate': 'other'}] * 3)

    def test_written_form_is_not_assumed_observed_sex(self):
        transformed = io_records([{'surname': 'Вымышлов', 'name': 'Тестимир', 'patronymic': 'Примерович'},
                                  {'surname': 'Тестова', 'name': 'Примерина', 'patronymic': 'Безсуффикса'}])
        self.assertEqual(transformed[0]['surname'], 'тестимир')
        self.assertEqual(transformed[0]['name'], 'примерович')
        self.assertEqual(transformed[0]['sex'], 2)
        self.assertIsNone(transformed[1]['sex'])


if __name__ == '__main__':
    unittest.main()
