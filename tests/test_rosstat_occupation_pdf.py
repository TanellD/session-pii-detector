"""Проверка извлечения агрегатов: синтетика и доступный локальный сборник."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from inspect_rosstat_occupation_pdf import extract, parse_table


class OccupationPdfTests(unittest.TestCase):
    def test_wrapped_label_and_distinct_conditionals(self):
        text = "  Синтетическая группа\n  занятий    100    40    60    10,0    8,0    12,0    40,0    60,0\n"
        rows = parse_table(text, 76, 0)
        self.assertEqual(rows[0]["label"], "Синтетическая группа занятий")
        self.assertEqual(rows[0]["all"], 100000)
        self.assertEqual(rows[0]["published_percent_occupation_among_employed"]["male"], 8)
        self.assertEqual(rows[0]["published_percent_sex_within_occupation"]["male"], 40)

    @unittest.skipUnless(list(Path("docs/project/drafts").glob("*(2024).pdf")), "Локальный PDF отсутствует")
    def test_local_verified_table(self):
        paths = list(Path("docs/project/drafts").glob("*(2024).pdf"))
        if len(paths) != 1:
            self.skipTest("Несколько PDF: требуется явный выбор")
        result = extract(paths[0])
        self.assertEqual(len(result["rows"]), 43)
        self.assertEqual(result["reference_year"], 2023)
        by_label = {row["label"]: row for row in result["rows"]}
        self.assertEqual(len(by_label), len(result["rows"]))
        self.assertEqual(by_label["Занятые – всего"]["all"], 73636000)
        self.assertEqual(by_label["Специалисты в области образования"]["female"], 3963000)
        # Опубликованные округлённые значения сохраняются, а не исправляются до точного равенства.
        self.assertEqual(by_label["Руководители"]["all"], 3556000)
        self.assertEqual(by_label["Руководители"]["male"] + by_label["Руководители"]["female"], 3557000)


if __name__ == "__main__":
    unittest.main()
