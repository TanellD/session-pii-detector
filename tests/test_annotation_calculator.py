"""Сессионные правила и расчёты; все обозначения людей/контактов синтетические."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch
import json

import pyarrow as arrow
import pyarrow.parquet as parquet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from annotation_calculator import Calculator, normalize_state, address_suggestions
from audit_name_frequencies import audit
from build_annotation_geography_reference import population_band
from build_housing_occupancy_reference import extract_counts


def fact(kind, value="[СИНТЕТИКА]", person="p1", **extra):
    return {"type": kind, "value": value, "person_id": person, **extra}


class CalculatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path("data/reference/annotation-geography-vpn2020.json").exists():
            raise unittest.SkipTest("Постройте локальные агрегированные справочники")
        cls.calculator = Calculator()

    def test_contact_pair_requires_same_person_and_personal_complete_contacts(self):
        phone = fact("phone", complete=True, usage="personal")
        self.assertFalse(self.calculator.session([phone])["session_alert"])
        email = fact("email", person="p2", complete=True, usage="personal")
        self.assertFalse(self.calculator.session([phone, email])["session_alert"])
        email["person_id"] = "p1"
        self.assertTrue(self.calculator.session([phone, email])["session_alert"])
        email["usage"] = "shared"
        self.assertIsNone(self.calculator.session([phone, email])["session_alert"])

    def test_name_pairs_and_approved_contacts_in_both_orders(self):
        for kinds, marker in ((("first_name", "last_name"), "fi"), (("first_name", "patronymic"), "io"), (("last_name", "patronymic"), "fo")):
            pair = [fact(kind, uncertain=True) for kind in kinds]
            pair_result = self.calculator.person(pair)
            self.assertEqual(pair_result["active_name_marker"], {"type": marker, "zone": "orange"})
            self.assertIsNot(pair_result["alert"], True)
            for kind in ("phone", "email"):
                contact = fact(kind, complete=True, usage="personal")
                for rows in (pair + [contact], [contact] + pair):
                    result = self.calculator.session(rows)
                    self.assertEqual(result["first_alert_attribute"], 3)
                    person = result["people"]["p1"]
                    self.assertTrue(person["alert"])
                    self.assertIsNone(person["candidate_count"])
                    self.assertIn("partial_name_plus_attribute_policy", person["rule_codes"])
                    self.assertEqual(person["uncertain_attributes_counted"], 2)

    def test_name_pair_requires_same_person_and_complete_distinct_parts(self):
        pair = [fact("first_name"), fact("patronymic")]
        for contact in (fact("phone", complete=False, usage="personal"),
                        fact("phone", complete=True, usage="shared"),
                        fact("phone", person=None, complete=True, usage="personal"),
                        fact("phone", person="p2", complete=True, usage="personal")):
            self.assertIsNot(self.calculator.session(pair + [contact])["session_alert"], True)
        contact = fact("phone", complete=True, usage="personal")
        for parts in ([fact("first_name"), fact("first_name")],
                      [fact("first_name"), fact("patronymic", complete=False)],
                      [fact("first_name"), fact("patronymic", person="p2")]):
            self.assertIsNot(self.calculator.session(parts + [contact])["session_alert"], True)

    def test_organization_remains_yellow_and_does_not_complete_name_rule(self):
        pair = [fact("last_name"), fact("first_name")]
        for employer in (fact("organization"), fact("organization", exact=False),
                         fact("organization", exact=True, role="mentioned"), fact("organization", exact=True)):
            result = self.calculator.person(pair + [employer])
            self.assertNotIn("partial_name_plus_attribute_policy", result["rule_codes"])
            self.assertIsNot(result["alert"], True)

    def test_name_pair_full_address_is_qualitative_and_full_name_does_not_double_count(self):
        pair = [fact("first_name"), fact("patronymic")]
        for address in (fact("address", completeness="partial", housing_type="apartment"),
                        fact("address", completeness="full", role="work")):
            self.assertNotIn("partial_name_plus_attribute_policy", self.calculator.person(pair + [address])["rule_codes"])
        for address in (fact("address", completeness="full", housing_type=""),
                        fact("address", completeness="full", housing_type="apartment_building", house_flat_count=100000)):
            result = self.calculator.person(pair + [address])
            self.assertTrue(result["alert"])
            self.assertIsNone(result["candidate_count"])
            self.assertEqual(result["alert_reason"], "combination_rule")
        address = fact("address", completeness="full", housing_type="apartment")
        self.assertIn("partial_name_plus_attribute_policy", self.calculator.person(pair + [address])["rule_codes"])
        full = pair + [fact("last_name")]
        self.assertIsNot(self.calculator.person(full)["alert"], True)
        result = self.calculator.person(full + [fact("email", complete=True, usage="personal")])
        self.assertTrue(result["alert"])
        self.assertEqual(result["active_attribute_types"], ["email", "fio"])
        self.assertEqual(result["active_name_marker"], {"type": "fio", "zone": "orange"})

    def test_wide_or_derived_attribute_does_not_complete_name_rule(self):
        pair = [fact("first_name"), fact("patronymic")]
        for additional in (fact("sex", "female"), fact("geography"), fact("occupation", "[НЕИЗВЕСТНО]"), fact("first_name")):
            self.assertNotIn("partial_name_plus_attribute_policy", self.calculator.person(pair + [additional])["rule_codes"])

    def test_employer_occupation_joint_sex_share_and_uncertain(self):
        result = self.calculator.session([fact("occupation", "программист", uncertain=True),
                                          fact("sex", "female"), fact("organization")])
        person = result["people"]["p1"]
        expected = 1 + (self.calculator.policy["employer_size"]["value"] - 1) * 210000 / 73636000
        self.assertAlmostEqual(person["candidate_count"], expected)
        self.assertEqual(person["uncertain_attributes_counted"], 1)
        self.assertTrue(person["alert"])

    def test_ambiguous_and_unknown_profession_never_become_zero(self):
        for occupation in ("менеджер", "[НЕИЗВЕСТНАЯ-ДОЛЖНОСТЬ]"):
            person = self.calculator.person([fact("occupation", occupation)])
            self.assertIsNone(person["candidate_count"])
            self.assertIsNone(person["alert"])

    def test_small_settlement_fallback_requires_confirmation_and_source(self):
        geography = fact("geography", "[СИНТЕТИЧЕСКИЙ НАСЕЛЁННЫЙ ПУНКТ]")
        self.assertIsNone(self.calculator.person([geography])["candidate_count"])
        geography["specific_settlement_under_5000_confirmed"] = True
        self.assertIsNone(self.calculator.person([geography])["candidate_count"])
        geography["population_bound_source"] = "[СИНТЕТИЧЕСКИЙ ИСТОЧНИК С ДАТОЙ]"
        result = self.calculator.person([geography, fact("sex", "female")])
        self.assertEqual(result["candidate_count"], 5000)
        self.assertFalse(result["alert"])
        geography["role"] = "работа"
        self.assertIsNone(self.calculator.person([geography])["candidate_count"])

    def test_verified_population_precedes_small_settlement_fallback(self):
        country = next(row for row in self.calculator.geo["rows"] if row["kind"] == "country")
        geography = fact("geography", geography_id=country["id"],
                         specific_settlement_under_5000_confirmed=True, population_bound_source="[СИНТЕТИКА]")
        self.assertEqual(self.calculator.person([geography])["candidate_count"], country["all"])

    def test_address_alone_and_with_components_exemption(self):
        address = fact("address", completeness="full", housing_type="apartment")
        self.assertFalse(self.calculator.person([address])["alert"])
        country = next(r for r in self.calculator.geo["rows"] if r["kind"] == "country")
        self.assertFalse(self.calculator.person([address, fact("geography", geography_id=country["id"])])["alert"])
        self.assertTrue(self.calculator.person([address, fact("sex", "female")])["alert"])
        unknown = fact("address", completeness="full", housing_type="")
        self.assertIsNone(self.calculator.person([unknown, fact("sex", "female")])["alert"])

    def test_sufficient_combination_survives_additional_unsupported_geography(self):
        rows = [fact("occupation", "программист"), fact("organization"), fact("geography")]
        self.assertTrue(self.calculator.person(rows)["alert"])

    def test_whole_building_uses_apartment_count_and_preserves_address_exception(self):
        address = fact("address", completeness="full", housing_type="apartment_building", house_flat_count=10)
        result = self.calculator.person([address])
        mean = next(row["mean_residents_per_occupied_unit"] for row in self.calculator.housing["rows"]
                    if row["region"] == "Российская Федерация" and row["settlement"] == "all" and row["housing_type"] == "apartment")
        self.assertAlmostEqual(result["candidate_count"], 10 * mean)
        self.assertFalse(result["alert"])
        self.assertIn("Все квартиры", result["estimates"][0]["occupancy_assumption"])
        phone = fact("phone", complete=True, usage="personal")
        self.assertTrue(self.calculator.person([address, phone])["alert"])
        address["house_flat_count"] = 100
        self.assertFalse(self.calculator.person([address, phone])["alert"])
        address["housing_type"] = "apartment"
        self.assertAlmostEqual(self.calculator.person([address])["candidate_count"], mean)
        address["housing_type"] = "apartment_building"
        address["resolved_level"] = "apartment"
        self.assertIsNone(self.calculator.person([address, phone])["candidate_count"])

    def test_missing_or_invalid_apartment_count_never_becomes_small_estimate(self):
        for value in (None, 0, -1, True, "10", 2.5):
            address = fact("address", completeness="full", housing_type="apartment_building", house_flat_count=value)
            result = self.calculator.person([address, fact("phone", complete=True, usage="personal")])
            self.assertIsNone(result["candidate_count"])
            self.assertIsNone(result["alert"])

    def test_latest_value_and_historical_first_alert(self):
        rows = [fact("occupation", "программист"), fact("organization"), fact("occupation", "[НЕИЗВЕСТНО]")]
        result = self.calculator.session(rows)
        self.assertEqual(len(rows), 3)
        self.assertEqual(result["first_alert_attribute"], 2)
        self.assertTrue(result["session_alert"])
        self.assertIsNone(result["people"]["p1"]["alert"])

    def test_full_name_replaces_parts_without_mutating_mentions(self):
        rows = [fact("first_name", "[ИМЯ]"), fact("last_name", "[ФАМИЛИЯ]"), fact("patronymic", "[ОТЧЕСТВО]")]
        self.assertEqual([f["type"] for f in normalize_state(rows)], ["fio"])
        self.assertEqual(len(rows), 3)
        self.assertIsNone(self.calculator.session(rows)["session_alert"])

    def test_component_correction_updates_explicit_full_name_without_mutating_history(self):
        rows = [fact("fio", "Тестов Тестимир Макетович"), fact("first_name", "Образимир")]
        result = normalize_state(rows)
        self.assertEqual(result[0]["value"], "Тестов Образимир Макетович")
        self.assertEqual(rows[0]["value"], "Тестов Тестимир Макетович")

    def test_candidate_comparison_is_explicit_and_does_not_round(self):
        original = self.calculator.policy.get("candidate_comparison", "le")
        try:
            self.calculator.policy["candidate_comparison"] = "lt"
            self.assertTrue(self.calculator.within_threshold(99.99))
            self.assertFalse(self.calculator.within_threshold(100))
            self.calculator.policy["candidate_comparison"] = "le"
            self.assertTrue(self.calculator.within_threshold(100))
            self.assertFalse(self.calculator.within_threshold(100.01))
            self.assertFalse(self.calculator.within_threshold(0))
        finally:
            self.calculator.policy["candidate_comparison"] = original

    def test_unassigned_red_triggers_but_other_unassigned_is_not_attached(self):
        self.assertTrue(self.calculator.session([fact("snils", person=None, complete=True)])["session_alert"])
        result = self.calculator.session([fact("occupation", "программист", person=None)])
        self.assertEqual(result["people"], {})
        self.assertIsNone(result["session_alert"])


class ReferenceTests(unittest.TestCase):
    def test_address_adapter_synthetic_response_and_no_occupancy_invention(self):
        response = {"suggestions": [{"value": "[СИНТЕТИЧЕСКИЙ-АДРЕС]", "data": {
            "house": "[ДОМ]", "flat": "[КВАРТИРА]", "fias_id": "synthetic-gar-id", "house_flat_count": "10"}}]}
        with TemporaryDirectory() as directory:
            (Path(directory) / ".env").write_text("DADATA_API_KEY=synthetic-token\n", encoding="utf-8")
            with patch("annotation_calculator.ROOT", Path(directory)), patch("annotation_calculator.urlopen", return_value=BytesIO(json.dumps(response).encode())):
                result = address_suggestions("[СИНТЕТИЧЕСКИЙ-АДРЕС]")
        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["suggestions"][0]["resolved_level"], "apartment")
        self.assertEqual(result["suggestions"][0]["completeness"], "full")
        self.assertEqual(result["suggestions"][0]["house_flat_count"], 10)
        self.assertNotIn("synthetic-token", str(result))
        self.assertNotIn("residents", result["suggestions"][0])

    def test_population_boundaries_and_unknown(self):
        self.assertIsNone(population_band(None))
        self.assertEqual(population_band(999), "under_1000")
        self.assertEqual(population_band(1000), "1000_to_10000")
        self.assertEqual(population_band(500000), "500000_to_1000000")
        self.assertEqual(population_band(1000000), "at_least_1000000")

    def test_housing_includes_multiple_households_and_five_plus_people(self):
        # Два помещения: одно с домохозяйством из пяти человек, другое с двумя домохозяйствами / шестью жителями.
        row = ["[ТИП]", 2, 1, 0, 0, 0, 0, 1, 5, 1, 6, 0, 0, 0, 0]
        result = extract_counts(row)
        self.assertEqual(result["residents"], 11)
        self.assertEqual(result["mean_residents_per_occupied_unit"], 5.5)

    def test_name_audit_returns_no_name_keys(self):
        buffer = BytesIO()
        parquet.write_table(arrow.table({"text": ["[ИМЯ-А]", "[имя-а]", ""],
                                         "count": [2, 1, 1], "gender": ["m", "m", None]}), buffer)
        result = audit(buffer.getvalue())
        self.assertEqual(result["sum_counts"], 4)
        self.assertEqual(result["normalized_duplicate_groups"], 1)
        self.assertEqual(result["missing_labels"], 1)
        self.assertNotIn("[ИМЯ-А]", str(result))


if __name__ == "__main__":
    unittest.main()
