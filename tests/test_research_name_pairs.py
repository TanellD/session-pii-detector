"""Проверки статистики на синтетических категориях и вымышленных ФИО."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from research_name_pairs import descriptive, heldout, parse_fns_xml


class NamePairResearchTests(unittest.TestCase):
    def test_independent_cube_has_zero_dependence(self):
        rows = [(f, i, o) for f in ("f1", "f2") for i in ("i1", "i2") for o in ("o1", "o2")]
        result = descriptive(rows)
        self.assertAlmostEqual(result["FI_mutual_information_bits"], 0)
        self.assertAlmostEqual(result["FIO_residual_after_FI_bits"], 0)

    def test_pair_dependence_does_not_explain_xor_triple(self):
        rows = [(str(f), str(i), str(f ^ i)) for f in range(2) for i in range(2)]
        result = descriptive(rows)
        self.assertAlmostEqual(result["FI_mutual_information_bits"], 0)
        self.assertAlmostEqual(result["FIO_residual_after_FI_bits"], 1)

    def test_holdout_handles_unseen_names_without_zero_probabilities(self):
        rows = [(f"f{i}", f"i{i}", f"o{i}") for i in range(100)]
        result = heldout(rows)
        self.assertEqual(result["coverage"]["any_part_unseen_in_train"], 20)
        self.assertEqual(result, heldout(list(reversed(rows))))
        self.assertTrue(all(0 < v < 100 for v in result["mean_log_loss_bits"].values()))

    def test_xml_counts_ip_once_not_company_or_same_name(self):
        raw = '''<Файл><Документ><ИПВклМСП ИННФЛ="synthetic-id-1"><ФИОИП
          Фамилия="Вымышлов" Имя="Тестимир" Отчество="Примерович"/></ИПВклМСП></Документ>
          <Документ><ИПВклМСП ИННФЛ="synthetic-id-1"><ФИОИП
          Фамилия="Вымышлов" Имя="Тестимир" Отчество="Примерович"/></ИПВклМСП></Документ>
          <Документ><ИПВклМСП ИННФЛ="synthetic-id-2"><ФИОИП
          Фамилия="Вымышлов" Имя="Тестимир" Отчество="Примерович"/></ИПВклМСП></Документ>
          <Документ><ЮЛ><ФИО Фамилия="Вымышлов" Имя="Тестимир" Отчество="Примерович"/></ЮЛ></Документ></Файл>'''.encode()
        rows = parse_fns_xml(raw, set(), 20)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(set(rows[0]), {"surname", "name", "patronymic"})


if __name__ == "__main__":
    unittest.main()
