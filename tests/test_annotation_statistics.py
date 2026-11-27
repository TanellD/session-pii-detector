"""Проверки агрегирования на синтетических данных без ПДн."""

from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_annotation_population_reference import parse_groups
from build_regional_statistics_registry import normalized
from summarize_organization_sizes import describe, summarize


class OrganizationStatisticsTests(unittest.TestCase):
    def test_mean_median_and_employee_weighting(self):
        stats = describe(Counter({2: 1, 8: 1}))
        self.assertEqual(stats["mean_by_organization"], 5)
        self.assertEqual(stats["median_by_organization"], 5)
        self.assertEqual(stats["size_weighted_mean"], 6.8)
        self.assertIsNone(describe(Counter()))

    def test_duplicate_missing_and_zero_records(self):
        documents = []
        for key, size in [("synthetic-a", 2), ("synthetic-b", 8),
                          ("synthetic-a", 2), ("synthetic-c", 0)]:
            documents.append(f'<Документ ДатаСост="synthetic-date"><СведНП ИННЮЛ="{key}"/>'
                             f'<СведССЧР КолРаб="{size}"/></Документ>')
        documents.append('<Документ ДатаСост="synthetic-date"/>')
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("synthetic.xml", ("<Файл>" + "".join(documents) + "</Файл>").encode("utf-8"))
        result = summarize(buffer.getvalue())
        group = result["groups_by_reporting_date"]["synthetic-date"]
        self.assertEqual(group["including_zero"]["reporting_organizations"], 3)
        self.assertEqual(group["positive_only"]["reporting_organizations"], 2)
        self.assertEqual(group["positive_only"]["mean_by_organization"], 5)
        self.assertEqual(result["duplicate_records_skipped"], 1)
        self.assertEqual(result["incomplete_records_skipped"], 1)


class PopulationStatisticsTests(unittest.TestCase):
    def test_unknown_region_does_not_inherit_previous_region(self):
        workbook = Workbook()
        sheet = workbook.active
        for row in [("Синтетический регион",), ("Городское и сельское население", 10, 4, 6),
                    ("0-4", 10, 4, 6), ("Неизвестная территория",),
                    ("Городское и сельское население", 20, 9, 11), ("0-4", 20, 9, 11)]:
            sheet.append(row)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            workbook.save(path)
            rows = parse_groups(path, {normalized("Синтетический регион"): "Синтетический регион"})
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(r["count"] for r in rows if r["sex"] == "all"), 20)

    @unittest.skipUnless(Path("data/reference/annotation-population-vpn2020.json").exists(),
                         "Локальный агрегированный справочник не построен")
    def test_local_reference_totals(self):
        reference = json.loads(Path("data/reference/annotation-population-vpn2020.json").read_text(encoding="utf-8"))
        counts = {(r["region"], r["settlement"], r["age_band"], r["sex"]): r["count"]
                  for r in reference["rows"]}
        self.assertEqual(len(counts), len(reference["rows"]))
        for (region, settlement, age, sex), count in counts.items():
            if sex == "all":
                self.assertEqual(count, counts[(region, settlement, age, "male")]
                                 + counts[(region, settlement, age, "female")])
            if settlement == "all":
                self.assertEqual(count, counts[(region, "urban", age, sex)]
                                 + counts[(region, "rural", age, sex)])
        for row in reference["household_rows"]:
            self.assertEqual(row["households"], sum(row["size_bins"].values()))


if __name__ == "__main__":
    unittest.main()
