import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_llm_annotation_pilot import build
from run_llm_annotation_pilot import annotation_schema, checked_annotation
from export_annotation_context import build_context
from annotation_calculator import Calculator
import json


class AnnotationPilotTests(unittest.TestCase):
    def test_llm_context_uses_evaluator_policy_without_enabling_prepared_means(self):
        context = build_context()
        self.assertEqual(context["policy"], Calculator().policy)
        self.assertFalse(context["availability"]["prepared_size_means_connected"])
        self.assertFalse(context["policy"]["annotation_semantics"]["unlinked_entities_trigger_verdict_uncertainty"])

    def test_saved_llm_pass_preserves_input_and_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            build(output)
            raw = json.loads((output/"sessions.json").read_text(encoding="utf-8"))["sessions"]
            docs = json.loads((output/"in-session-annotations.json").read_text(encoding="utf-8"))["annotations"]
            for source, doc in zip(raw, docs):
                checked_annotation(doc, source)
            altered = copy.deepcopy(docs[0])
            altered["messages"][0]["text"] += "!"
            with self.assertRaises(ValueError):
                checked_annotation(altered, raw[0])

    def test_schema_excludes_composite_and_extra_entity_fields(self):
        schema = annotation_schema()
        entity = schema["properties"]["entities"]["items"]
        self.assertFalse(entity["additionalProperties"])
        self.assertNotIn("fio", entity["properties"]["type"]["enum"])
        self.assertEqual(set(entity["properties"]), {"id", "message_id", "start", "end", "type", "person_id"})


if __name__ == "__main__":
    unittest.main()
