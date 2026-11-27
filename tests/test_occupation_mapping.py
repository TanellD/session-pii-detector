"""Сопоставление не должно превращать неоднозначную должность в редкую профессию."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from estimate_occupation_candidates import OccupationReference, normalized


class OccupationMappingTests(unittest.TestCase):
    def setUp(self):
        self.mapping = json.loads(Path("configs/occupation-okz-mapping.json").read_text(encoding="utf-8"))
        # Синтетический справочник: проверяем выбор группы, а не опубликованные частоты.
        rows = [{"id": "synthetic_01", "all": 1000, "male": 500, "female": 500}]
        for suffix in self.mapping["row_to_okz"]:
            rows.append({"id": "synthetic_" + suffix, "label": "Синтетическая группа " + suffix,
                         "all": 100, "male": 40, "female": 60, "pdf_page": 0})
        self.reference = OccupationReference({"sha256": self.mapping["source_sha256"],
                                               "reference_year": 2023, "rows": rows}, self.mapping)

    def test_every_alias_is_normalized_and_uses_published_groups(self):
        valid = set(self.mapping["row_to_okz"].values())
        for alias, codes in self.mapping["aliases"].items():
            with self.subTest(alias=alias):
                self.assertEqual(alias, normalized(alias))
                self.assertTrue(codes)
                self.assertEqual(len(codes), len(set(codes)))
                self.assertTrue(set(codes) <= valid)

    def test_ambiguous_titles_require_choice_and_never_pick_smallest_group(self):
        for title in ("менеджер по продажам", "медсестра", "кладовщик", "логист", "дизайнер"):
            with self.subTest(title=title):
                result = self.reference.estimate(title)
                self.assertEqual(result["status"], "ambiguous")
                self.assertIsNone(result["candidate_count"])
                self.assertTrue(result["clarification"])

    def test_job_duties_distinguish_same_domain(self):
        for title, code in {"системный администратор": "25", "администратор гостиницы": "42",
                            "инженер-электрик": "21", "техник-электрик": "31",
                            "электромонтажник": "74", "графический дизайнер": "21"}.items():
            with self.subTest(title=title):
                self.assertEqual(self.reference.estimate(title)["okz_group"], code)


if __name__ == "__main__":
    unittest.main()
