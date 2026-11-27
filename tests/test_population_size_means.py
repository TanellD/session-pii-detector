import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from research_population_size_means import build, housing_statistics, object_type, statistics


class SizeMeansTests(unittest.TestCase):
    def test_resident_and_object_sampling_are_different(self):
        r = statistics([0, 10, 90])
        self.assertAlmostEqual(r["object_mean"], 100/3)
        self.assertEqual(r["object_median"], 10)
        self.assertEqual(r["resident_weighted_mean"], 82)
        self.assertIsNone(statistics([0])["resident_weighted_mean"])

    def test_census_type_has_priority_over_modern_geocoder(self):
        self.assertEqual(object_type("пгт Макетный", "город"), "ПГТ/рабочий посёлок")
        self.assertEqual(object_type("село Примерное", None), "село")

    def test_nested_settlement_does_not_double_count_federal_city(self):
        def row(level, name, region, n, code):
            return dict(object_level=level, object_name=name, region=region,
                        population=n, oktmo=code, settlement_type_full_dadata=None)
        rows = [row("Страна", "Итог", "Итог", 100, "0"),
                row("Город федерального значения", "Макет", "Макет", 100, "1"),
                row("Населенный пункт", "село Примерное", "Макет", 20, "2")]
        r = build(rows)
        self.assertEqual(r["audit"]["selected_objects"], 1)
        rows[-1]["region"] = "Другая"
        with self.assertRaises(ValueError):
            build(rows)

    def test_housing_excludes_unknown_flats_and_inactive_buildings(self):
        def row(code, flats, stage="Эксплуатация"):
            return dict(gisgkh_guid=code, flats=flats, house_type="Многоквартирный",
                        life_cycle_stage=stage, region_name="Синтетический регион")
        r = housing_statistics([row("a", 10), row("b", 90), row("c", None),
                                row("d", 0), row("e", 100, "Снос")])
        self.assertEqual(r["quality"]["included_buildings"], 2)
        self.assertEqual(r["quality"]["unknown_or_nonpositive_flats"], 2)
        group = next(g for g in r["groups"] if g["region"] == "Вся доступная выборка")
        self.assertAlmostEqual(group["estimated_residents_resident_weighted_mean"], 82*2.3152949159)
        with self.assertRaises(ValueError):
            housing_statistics([row("same", 10), row("same", 10)])


if __name__ == "__main__":
    unittest.main()
