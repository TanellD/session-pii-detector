from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from annotation_calculator import Calculator
from estimate_address_population import estimate_resolved, estimate_from_query, lookup_house


class AddressPopulationTests(unittest.TestCase):
    def setUp(self):
        self.calculator = Calculator(policy_only=True)
        self.address = {"value": "г. Макетоград, ул. Примерная, д. 1", "unrestricted_value": "[СИНТЕТИЧЕСКИЙ-ПОЛНЫЙ-АДРЕС]",
                        "completeness": "full", "resolved_level": "house", "house_fias_id": "synthetic-house"}

    def test_apartment_mean_from_config_without_reference_files(self):
        address = {**self.address, "has_flat": True, "flat_type": "кв", "resolved_level": "apartment"}
        result = estimate_resolved(self.calculator, address)
        self.assertEqual(result["housing_type"], "apartment")
        self.assertAlmostEqual(result["population_estimate"], 99496891 / 42973744)
        self.assertFalse(result["address_alone_alert"])

    def test_building_count_multiplies_apartment_mean_and_assumption_is_visible(self):
        result = estimate_resolved(self.calculator, {**self.address, "house_flat_count": 10})
        self.assertEqual(result["housing_type"], "apartment_building")
        self.assertAlmostEqual(result["population_estimate"], 10 * 99496891 / 42973744)
        self.assertTrue(result["assumptions"])

    def test_individual_requires_explicit_registry_type(self):
        result = estimate_resolved(self.calculator, self.address,
             {"status": "matched", "house_type": "Индивидуальный жилой дом", "state": "Эксплуатация"})
        self.assertAlmostEqual(result["population_estimate"], 42083643 / 15518553)
        for extra in ({}, {"house_flat_count": 1}, {"house_flat_count": 0}, {"house_flat_count": True}):
            self.assertIsNone(estimate_resolved(self.calculator, {**self.address, **extra})["population_estimate"])
        result = estimate_resolved(self.calculator, self.address,
             {"status": "matched", "house_type": "Жилой", "state": "Эксплуатация"})
        self.assertEqual(result["reason"], "housing_type_unknown")

    def test_rooms_partial_addresses_conflicts_and_inactive_buildings_are_unknown(self):
        for address, building in [
            ({**self.address, "has_room": True}, None),
            ({**self.address, "country_iso_code": "BY"}, None),
            ({**self.address, "completeness": "partial"}, None),
            ({**self.address, "house_flat_count": 5}, {"status": "matched", "flats": 10, "state": "Эксплуатация"}),
            (self.address, {"status": "matched", "state": "Снос"}),
            (self.address, {"status": "ambiguous"})]:
            self.assertIsNone(estimate_resolved(self.calculator, address, building)["population_estimate"])

    def test_unique_suggestion_is_refined_using_unrestricted_value(self):
        calls = []
        def suggest(query, count):
            calls.append((query, count))
            return {"status": "selection_required", "suggestions": [{**self.address, "house_flat_count": 12}]}
        with patch("estimate_address_population.lookup_house", return_value={"status": "not_found"}):
            result = estimate_from_query(self.calculator, "[СИНТЕТИЧЕСКИЙ-ЗАПРОС]", suggest)
        self.assertEqual(calls, [("[СИНТЕТИЧЕСКИЙ-ЗАПРОС]", 5), (self.address["unrestricted_value"], 1)])
        self.assertEqual(result["status"], "estimated")

    def test_ambiguous_suggestions_require_selection_and_do_not_estimate(self):
        result = estimate_from_query(self.calculator, "[СИНТЕТИКА]", lambda *_: {
            "status": "selection_required", "suggestions": [self.address, {**self.address, "value": "Другой адрес"}]})
        self.assertEqual(result["status"], "selection_required")
        self.assertIsNone(result["population_estimate"])

    def test_readonly_house_lookup_does_not_select_arbitrary_duplicate(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/"houses.sqlite"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE houses (house_id TEXT,house_type TEXT,flats INTEGER,state TEXT,region TEXT)")
            db.execute("CREATE TABLE metadata (key TEXT,value TEXT)")
            db.execute("INSERT INTO houses VALUES ('synthetic','Многоквартирный',10,'Эксплуатация','Синтетический регион')")
            db.commit()
            self.assertEqual(lookup_house("synthetic",path)["flats"], 10)
            db.execute("INSERT INTO houses VALUES ('synthetic','Многоквартирный',20,'Эксплуатация','Синтетический регион')")
            db.commit()
            self.assertEqual(lookup_house("synthetic",path)["status"], "ambiguous")
            db.close()


if __name__ == "__main__":
    unittest.main()
