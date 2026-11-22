"""Только вымышленные строки и категории, без внешней сети."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pilot_public_fullname_statistics import parse_candidates, summarize
from batch_public_fullname_statistics import parse_fio_table, YearStrongNames, fetch


class PublicFullnamePilotTests(unittest.TestCase):
    def test_access_denial_never_retried(self):
        denied = HTTPError("https://sa.cs.msu.ru/", 403, "denied", {}, None)
        with patch("batch_public_fullname_statistics.urlopen", side_effect=denied) as request:
            with self.assertRaises(HTTPError):
                fetch("https://sa.cs.msu.ru/node/466")
            self.assertEqual(request.call_count, 1)

    def test_permutation_baseline_does_not_depend_on_document_row_order(self):
        rows = [("f1", "i1", "o1"), ("f2", "i1", "o2"), ("f1", "i2", "o1")]
        self.assertEqual(summarize(rows, permutations=5), summarize(list(reversed(rows)), permutations=5))

    def test_table_uses_only_fio_column_not_supervisor_or_email(self):
        html = ('<p>Вымышлов Тестимир Примерович</p><table>'
                '<tr><th>№</th><th>ФИО</th><th>Научный руководитель</th></tr>'
                '<tr><td>1</td><td>Вымышлов Тестимир Примерович</td>'
                '<td>Вымышлова Тестимира Примеровна</td></tr>'
                '<tr><td>2</td><td>Две Части</td><td>Вымышлова Тестимира Примеровна</td></tr></table>')
        years, quality = parse_fio_table(html, 2024)
        self.assertEqual(len(years[2024]), 1)
        self.assertEqual(quality[2024]["eligible_name_cells"], 2)
        self.assertEqual(quality[2024]["unsupported_name_cells"], 1)

    def test_year_scoped_parser_excludes_history_and_preserves_unsupported_rows(self):
        html = ('<div class="entry-content"><h3>1921</h3><strong>Вымышлов Тестимир Примерович</strong>'
                '<h3>2024</h3><strong>Вымышлова Тестимира Примеровна</strong>'
                '<strong>Вымышлов (Другая) Тестимир Примерович</strong>'
                '<h3>Другой раздел</h3><strong>Вымышлов Тестимир Примерович</strong></div>')
        parser = YearStrongNames(2010, 2025)
        parser.feed(html)
        self.assertEqual(set(parser.by_year), {2024})
        self.assertEqual(len(parser.by_year[2024]), 1)
        self.assertEqual(parser.quality[2024]["eligible_name_cells"], 2)
        self.assertEqual(parser.quality[2024]["unsupported_name_cells"], 1)

    def test_navigation_and_other_fields_not_extracted(self):
        html = ('<div>Вымышлов Тестимир Примерович</div>'
                '<div class="field field-name-body"><p>Вымышлов Тестимир Примерович</p>'
                '<p>ФИО скрыто</p><p>Вымышлова Тестимира Примеровна</p></div>'
                '<p>Вымышлов Тестимир Примерович</p>')
        rows, rejected = parse_candidates(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rejected, 1)

    def test_repeated_fullname_not_claimed_to_be_one_person(self):
        report = summarize([("f", "i", "o")] * 2, permutations=3)
        self.assertEqual(report["recognized_fullname_rows"], 2)
        self.assertEqual(report["distinct_written_fullnames"], 1)
        self.assertNotIn('"f"', str(report))

    def test_unique_categories_can_create_large_tc_without_detectable_dependence(self):
        report = summarize([(str(i), str(i), str(i)) for i in range(5)], permutations=5)
        baseline = report["permutation_baseline"]["total_correlation_bits"]
        self.assertGreater(baseline["observed"], 0)
        self.assertAlmostEqual(baseline["observed"], baseline["null_mean"])
        self.assertEqual(baseline["permutation_upper_tail_p"], 1)


if __name__ == "__main__":
    unittest.main()
