import json
import unittest
from unittest.mock import Mock, patch

from benchmarks.adapter.adapter import extract
from benchmarks.adapter.adapter_contract import CONTRACT_VERSION
from benchmarks.adapter.adapter_gliner import extract_message


class AdapterChecks(unittest.TestCase):
    def test_adapter_contract_and_stable_ids(self):
        message = "Пишите на lada@example.com."
        first = extract(message)
        second = extract(message)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        mention = first[0]
        self.assertEqual(mention.type, "EMAIL")
        self.assertEqual(mention.text, "lada@example.com")
        self.assertIsNone(mention.confidence)
        self.assertEqual(mention.confidence_kind, "none")
        self.assertEqual(mention.source, "regex")

    def test_adapter_full_name_rules_and_inn_priority(self):
        text = "Меня зовут Лада Олеговна Макетова."
        value = "Лада Олеговна Макетова"
        start = text.index(value)
        full = extract(
            text,
            gliner_entities=[
                {
                    "label": "person",
                    "start": start,
                    "end": start + len(value),
                    "score": 0.9,
                }
            ],
        )
        self.assertEqual(
            [m.type for m in full], ["FIRST_NAME", "PATRONYMIC", "LAST_NAME"]
        )
        inn = extract("Тестовый ИНН: 000000123411.")
        self.assertEqual([m.type for m in inn], ["INN"])

    def test_person_on_pronoun_suppressed(self):
        text = 'Я работаю в ООО "Макет".'
        mentions = extract(
            text,
            gliner_entities=[
                {
                    "label": "person",
                    "start": 0,
                    "end": 1,
                    "score": 0.78,
                }
            ],
        )
        types = [m.type for m in mentions]
        self.assertNotIn("PERSON", types)
        self.assertIn("ORGANIZATION", types)
        org = next(m for m in mentions if m.type == "ORGANIZATION")
        self.assertEqual(org.source, "regex")
        self.assertEqual(org.confidence_kind, "none")

    def test_model_threshold_override_and_config_fallback(self):
        model = Mock()
        model.predict_entities.return_value = []
        with patch(
            "benchmarks.adapter.adapter_gliner.get_model",
            return_value=(model, {"threshold": 0.5}, ["person"]),
        ):
            extract_message("", threshold=0.73)
            extract_message("")

        self.assertEqual(
            [
                call.kwargs["threshold"]
                for call in model.predict_entities.call_args_list
            ],
            [0.73, 0.5],
        )

    def test_synthetic_attributes_has_no_t_gold(self):
        with open(
            "benchmarks/adapter/data/synthetic-attributes.json", encoding="utf-8"
        ) as fixture:
            payload = json.load(fixture)
        self.assertTrue(payload["synthetic"])
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertNotIn("t_gold", payload)


if __name__ == "__main__":
    unittest.main()
