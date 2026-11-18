"""Синтетические имена; персональные страницы не используются."""

from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pilot_wikipedia_fullnames import explicit_triple, normalize, conditional_diagnostics
import pilot_wikipedia_fullnames as pilot


class WikipediaFullnameTests(unittest.TestCase):
    def test_observed_fullname_orders_and_stress(self):
        expected = ("вымышлов", "тестимир", "примерович")
        self.assertEqual(explicit_triple("Вымышлов, Тестимир Примерович",
                                        "Тести́мир Приме́рович Вы́мышлов (род.) — персонаж."), expected)
        self.assertEqual(explicit_triple("Вымышлов Тестимир Примерович",
                                        "Вымышлов Тестимир Примерович — персонаж."), expected)

    def test_no_patronymic_inference_from_father_or_later_text(self):
        self.assertIsNone(explicit_triple("Вымышлов Тестимир", "Тестимир Вымышлов — сын Примера."))
        self.assertIsNone(explicit_triple("Вымышлов Тестимир", "Биография. Тестимир Примерович Вымышлов"))

    def test_conflicting_title_and_ambiguous_order_rejected(self):
        self.assertIsNone(explicit_triple("Другов Тестимир", "Тестимир Примерович Вымышлов"))
        self.assertIsNone(explicit_triple("Вымышлов Тестимир", "Тестимир Примерович Выдумович"))

    def test_normalization_preserves_yo_and_short_i(self):
        self.assertEqual(normalize("Ёй ёй"), "Ёй ёй")

    def test_conditional_diagnostics_separate_written_forms(self):
        rows = [("f1", "i1", "тестович"), ("f2", "i2", "примерович"),
                ("f3", "i3", "тестовна"), ("f4", "i4", "примеровна")]
        result = conditional_diagnostics(rows, permutations=3)
        self.assertEqual(result["feminine_form"]["rows"], 2)
        self.assertEqual(result["masculine_form"]["rows"], 2)
        self.assertNotIn("i1", str(result))

    def test_partial_attempt_preserves_completed_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            complete = Path(directory) / "complete.json"
            latest = Path(directory) / "latest.json"
            complete.write_text('{"status":"completed"}', encoding="utf-8")
            with patch.object(pilot, "OUTPUT", complete), patch.object(pilot, "LATEST", latest), \
                 patch.object(pilot, "collect", return_value={"status": "partial_article_access"}), \
                 patch.object(sys, "argv", ["pilot", "--download"]), redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as result:
                    pilot.main()
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(complete.read_text(), '{"status":"completed"}')
            self.assertIn("partial_article_access", latest.read_text())

    def test_access_denial_stops_subsequent_article_batches(self):
        rows = [{"person": {"value": "synthetic-item-" + str(i)},
                 "article": {"value": "https://ru.wikipedia.org/wiki/Вымышлов_Тестимир"},
                 **{key: {"value": "false"} for key in ("given", "family", "patronym")}}
                for i in range(21)]
        with patch.object(pilot, "fetch_json", side_effect=[
                ({"results": {"bindings": rows}}, "synthetic-hash"),
                HTTPError("https://ru.wikipedia.org/", 403, "denied", {}, None)]) as request, \
             redirect_stderr(StringIO()):
            result = pilot.collect()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["counts"]["unrequested_items_after_access_failure"], 1)
        self.assertEqual(result["counts"]["http_status_403"], 1)


if __name__ == "__main__":
    unittest.main()
