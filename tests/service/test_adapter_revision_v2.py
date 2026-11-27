"""Проверка семантических границ контактов и явного контекста ФИО."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from session_pii.adapter_revision_v2 import revise, contact_filter
from session_pii.components import DetectionResult, EmptyDetector, StubLinker
from session_pii.models import Message, Span, Verdict
from session_pii.pipeline import Pipeline


class AdapterRevisionV2Tests(unittest.TestCase):
    def span(self, text, value, kind):
        start = text.index(value)
        return Span(type=kind, start=start, end=start + len(value), score=.9)

    def values(self, text, result):
        return [(e.type, text[e.start:e.end]) for e in result.spans]

    def test_contact_removes_embedded_organization_age_and_name(self):
        text = 'Почта: sampleclub.21@example.invalid. Возраст: 31.'
        base = DetectionResult(tuple(self.span(text, value, kind) for value, kind in [
            ('sampleclub.21@example.invalid', 'email'), ('sampleclub', 'organization'),
            ('21', 'age'), ('example', 'person'), ('31', 'age')]))
        result, rejected = revise(text, base)
        self.assertEqual(self.values(text, result), [
            ('email', 'sampleclub.21@example.invalid'), ('age', '31')])
        self.assertEqual(sum(r['reason'] == 'semantic_attribute_inside_contact' for r in rejected), 3)

    def test_explicit_three_part_name_repairs_already_typed_spans(self):
        text = 'Я Пробов Макетослав Примерович.'
        base = DetectionResult(tuple(self.span(text, value, kind) for value, kind in [
            ('Пробов', 'first_name'), ('Макетослав', 'patronymic'), ('Примерович', 'last_name')]))
        result, _ = revise(text, base)
        self.assertEqual(self.values(text, result), [
            ('last_name', 'Пробов'), ('first_name', 'Макетослав'), ('patronymic', 'Примерович')])
        self.assertTrue(all(e.score is None and e.provenance[0].detector_version.endswith('v2') for e in result.spans))

    def test_reverse_name_order_and_context_only_extraction(self):
        text = 'Меня зовут Макетослав Примерович Пробов.'
        result, _ = revise(text, DetectionResult())
        self.assertEqual(self.values(text, result), [
            ('first_name', 'Макетослав'), ('patronymic', 'Примерович'), ('last_name', 'Пробов')])

    def test_unexplained_single_person_stays_person(self):
        text = 'Подпись: Макетослав.'
        result, _ = revise(text, DetectionResult((self.span(text, 'Макетослав', 'person'),)))
        self.assertEqual(self.values(text, result), [('person', 'Макетослав')])

    def test_quoted_organization_is_not_name_and_alias_is_person(self):
        text = 'ООО «Я Пробов Макетослав». Псевдоним «ПробныйПутник».'
        result, _ = revise(text, DetectionResult((self.span(text, 'ООО «Я Пробов Макетослав»', 'organization'),)))
        self.assertEqual(self.values(text, result), [
            ('organization', 'ООО «Я Пробов Макетослав»'), ('person', 'ПробныйПутник')])

    def test_full_pipeline_accepts_contact_after_filter(self):
        text = 'Почта: sampleclub@example.invalid.'
        result, _ = revise(text, DetectionResult((self.span(text, 'sampleclub@example.invalid', 'email'),
                                                self.span(text, 'sampleclub', 'organization'))))
        class Detector:
            def detect(self, message):
                return result
        step = Pipeline(Detector(), EmptyDetector(), StubLinker()).process(
            Message(message_id=1, text=text), (), Verdict())
        self.assertEqual([e.type for e in step.entities], ['email'])

    def test_partial_overlap_is_not_silently_discarded(self):
        text = 'sampleclub@example.invalid suffix'
        result, rejected = contact_filter(text, DetectionResult((
            self.span(text, 'sampleclub@example.invalid', 'email'),
            self.span(text, 'invalid suffix', 'organization'))))
        self.assertEqual(len(result.spans), 2)
        self.assertFalse(rejected)

    def test_contact_filter_preserves_documents_and_incomplete_status(self):
        text = '00000000000'
        base = DetectionResult((self.span(text, text, 'phone'), self.span(text, text, 'snils')),
                               False, ('model_incomplete',))
        result, rejected = contact_filter(text, base)
        self.assertEqual(result.spans, base.spans)
        self.assertFalse(result.complete)
        self.assertEqual(result.reasons, base.reasons)
        self.assertFalse(rejected)


if __name__ == '__main__':
    unittest.main()
